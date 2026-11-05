// Copyright 2026 Cinna Davis; PolyForm Noncommercial 1.0.0.
// Package acoustic is an experimental standalone acoustic kernel.
package acoustic

/*
#cgo CFLAGS: -O3 -ffp-contract=off -fno-fast-math
#cgo LDFLAGS: -lm
#include "core.h"
*/
import "C"
import (
	"fmt"
	"math"
	"unsafe"
)

const MaxSamples = 1048576

// Request holds SciPy PPoly coefficients, not raw audio. Callers establish motion support.
type Request struct {
	Times        []float64 `json:"times"`
	Observer     []float64 `json:"observer"`
	Position     []float64 `json:"position"`
	Knots        []float64 `json:"knots"`
	Packed       []float64 `json:"packed"`
	PackedKnots  []float64 `json:"packed_knots"`
	Origin       float64   `json:"origin"`
	Start        float64   `json:"start"`
	End          float64   `json:"end"`
	C            float64   `json:"c"`
	ArrivalStart float64   `json:"arrival_start"`
	ArrivalEnd   float64   `json:"arrival_end"`
}

// Solver owns reusable output/workspace. One per goroutine; results expire at next call.
type Solver struct {
	Factor, Emission, Distance []float64
	state                      []uint16
}

func ptr(v []float64) *C.double { return (*C.double)(unsafe.Pointer(&v[0])) }
func (s *Solver) Solve(r *Request) error {
	n, m, pm := len(r.Times), len(r.Knots)-1, len(r.PackedKnots)-1
	if n < 1 || n > MaxSamples || len(r.Observer) != 3 || m < 1 || m > 4096 || pm < 1 || pm > 4096 || len(r.Position) != 12*m || len(r.Packed) != 24*pm {
		return fmt.Errorf("invalid lengths or sample budget")
	}
	if cap(s.Factor) < n || cap(s.Emission) < n || cap(s.Distance) < n || cap(s.state) < n {
		s.Factor = make([]float64, n)
		s.Emission = make([]float64, n)
		s.Distance = make([]float64, n)
		s.state = make([]uint16, n)
	}
	s.Factor = s.Factor[:n]
	s.Emission = s.Emission[:n]
	s.Distance = s.Distance[:n]
	s.state = s.state[:n]
	status := C.dg_solve(ptr(r.Times), C.size_t(n), ptr(r.Observer), ptr(r.Position), C.size_t(len(r.Position)), ptr(r.Knots), C.size_t(m), ptr(r.Packed), C.size_t(len(r.Packed)), ptr(r.PackedKnots), C.size_t(pm), C.double(r.Origin), C.double(r.Start), C.double(r.End), C.double(r.C), C.double(r.ArrivalStart), C.double(r.ArrivalEnd), ptr(s.Factor), ptr(s.Emission), ptr(s.Distance), (*C.uint16_t)(unsafe.Pointer(&s.state[0])), C.size_t(n))
	if status != C.DG_OK {
		return fmt.Errorf("status %d (1 invalid, 2 reference fallback required): discard outputs", int(status))
	}
	return nil
}

// JSONValues maps unsupported NaNs to null.
func JSONValues(v []float64) []any {
	out := make([]any, len(v))
	for i, x := range v {
		if !math.IsNaN(x) && !math.IsInf(x, 0) {
			out[i] = x
		}
	}
	return out
}
