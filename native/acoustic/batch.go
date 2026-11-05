// Copyright 2026 Cinna Davis; PolyForm Noncommercial 1.0.0.
package acoustic

/*
#include "core.h"
*/
import "C"
import (
	"fmt"
	"runtime"
	"unsafe"
)

// Batch pins request/output arrays for repeated one-call native dispatch.
// Always defer Close after construction. Do not resize or concurrently mutate
// requests, outputs, or this batch. Scalar parameters are captured at creation;
// array elements may be updated between Solve calls. Every Solve revalidates.
type Batch struct {
	jobs     []C.dg_job
	solvers  []Solver
	requests []Request
	pins     runtime.Pinner
	closed   bool
}
type Result struct{ Factor, Emission, Distance []float64 }

func NewBatch(requests []Request) (batch *Batch, err error) {
	if len(requests) < 1 || len(requests) > 4096 {
		return nil, fmt.Errorf("batch size must be 1..4096")
	}
	b := &Batch{requests: append([]Request(nil), requests...), jobs: make([]C.dg_job, len(requests)), solvers: make([]Solver, len(requests))}
	defer func() {
		if err != nil {
			b.Close()
		}
	}()
	for i := range b.requests {
		r, s := &b.requests[i], &b.solvers[i]
		if err = s.Solve(r); err != nil {
			return nil, fmt.Errorf("request %d: %w", i, err)
		}
		for _, a := range [][]float64{r.Times, r.Observer, r.Position, r.Knots, r.Packed, r.PackedKnots, s.Factor, s.Emission, s.Distance} {
			b.pins.Pin(&a[0])
		}
		b.pins.Pin(&s.state[0])
		b.jobs[i] = C.dg_job{t: ptr(r.Times), n: C.size_t(len(r.Times)), observer: ptr(r.Observer), p: ptr(r.Position), plen: C.size_t(len(r.Position)), x: ptr(r.Knots), m: C.size_t(len(r.Knots) - 1), packed: ptr(r.Packed), packed_len: C.size_t(len(r.Packed)), packed_x: ptr(r.PackedKnots), pm: C.size_t(len(r.PackedKnots) - 1), origin: C.double(r.Origin), start: C.double(r.Start), end: C.double(r.End), c: C.double(r.C), arrival_start: C.double(r.ArrivalStart), arrival_end: C.double(r.ArrivalEnd), factor: ptr(s.Factor), tau: ptr(s.Emission), distance: ptr(s.Distance), state: (*C.uint16_t)(unsafe.Pointer(&s.state[0])), state_len: C.size_t(len(s.state))}
	}
	return b, nil
}
func (b *Batch) Solve() error {
	if b.closed || len(b.jobs) == 0 {
		return fmt.Errorf("batch is closed")
	}
	status := C.dg_solve_batch(&b.jobs[0], C.size_t(len(b.jobs)))
	if status != C.DG_OK {
		for i, j := range b.jobs {
			if j.result != C.DG_OK {
				return fmt.Errorf("request %d: status %d; discard batch outputs", i, int(j.result))
			}
		}
		return fmt.Errorf("invalid batch")
	}
	runtime.KeepAlive(b)
	return nil
}

// Results aliases batch-owned arrays. Only use after successful Solve.
func (b *Batch) Results() []Result {
	out := make([]Result, len(b.solvers))
	for i, s := range b.solvers {
		out[i] = Result{s.Factor, s.Emission, s.Distance}
	}
	return out
}
func (b *Batch) Close() {
	if !b.closed {
		b.pins.Unpin()
		b.closed = true
		b.jobs = nil
		b.solvers = nil
		b.requests = nil
	}
}
