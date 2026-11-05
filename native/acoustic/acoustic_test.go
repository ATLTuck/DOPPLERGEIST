package acoustic

import (
	"encoding/json"
	"math"
	"os"
	"sync"
	"testing"
)

func realRequest(t testing.TB) Request {
	t.Helper()
	b, e := os.ReadFile("real-request.json")
	if e != nil {
		t.Fatal(e)
	}
	var r []Request
	if e = json.Unmarshal(b, &r); e != nil {
		t.Fatal(e)
	}
	return r[0]
}
func TestRealGolden(t *testing.T) {
	r := realRequest(t)
	var s Solver
	if e := s.Solve(&r); e != nil {
		t.Fatal(e)
	}
	b, e := os.ReadFile("real-request.expected.json")
	if e != nil {
		t.Fatal(e)
	}
	var golden struct {
		Bits [][]uint64 `json:"bits"`
	}
	if e = json.Unmarshal(b, &golden); e != nil {
		t.Fatal(e)
	}
	for j, a := range [][]float64{s.Factor, s.Emission, s.Distance} {
		for i, x := range a {
			if math.Float64bits(x) != golden.Bits[j][i] {
				t.Fatalf("output %d sample %d differs", j, i)
			}
		}
	}
}
func TestInvalidLengths(t *testing.T) {
	r := realRequest(t)
	r.Observer = nil
	var s Solver
	if s.Solve(&r) == nil {
		t.Fatal("accepted invalid observer")
	}
	r = realRequest(t)
	r.Times = nil
	if s.Solve(&r) == nil {
		t.Fatal("accepted empty query")
	}
}
func TestFallback(t *testing.T) {
	r := realRequest(t)
	r.Times[0] = math.NaN()
	var s Solver
	if s.Solve(&r) == nil {
		t.Fatal("accepted NaN query")
	}
}
func TestBoundaryNaNs(t *testing.T) {
	r := realRequest(t)
	r.Times = []float64{math.Nextafter(r.ArrivalStart, math.Inf(-1)), r.ArrivalStart, r.ArrivalEnd, math.Nextafter(r.ArrivalEnd, math.Inf(1))}
	var s Solver
	if e := s.Solve(&r); e != nil {
		t.Fatal(e)
	}
	for _, v := range [][]float64{s.Factor, s.Emission, s.Distance} {
		if !math.IsNaN(v[0]) || !math.IsNaN(v[3]) || math.IsNaN(v[1]) || math.IsNaN(v[2]) {
			t.Fatal("boundary support changed")
		}
	}
}
func TestWarmSolveNoGoAllocations(t *testing.T) {
	r := realRequest(t)
	var s Solver
	if e := s.Solve(&r); e != nil {
		t.Fatal(e)
	}
	alloc := testing.AllocsPerRun(100, func() {
		if e := s.Solve(&r); e != nil {
			panic(e)
		}
	})
	if alloc != 0 {
		t.Fatalf("warm allocations %v", alloc)
	}
}
func BenchmarkRealSolve(b *testing.B) {
	r := realRequest(b)
	var s Solver
	if e := s.Solve(&r); e != nil {
		b.Fatal(e)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if e := s.Solve(&r); e != nil {
			b.Fatal(e)
		}
	}
}

func TestConcurrentIndependentSolvers(t *testing.T) {
	r := realRequest(t)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			var s Solver
			for j := 0; j < 100; j++ {
				if e := s.Solve(&r); e != nil {
					t.Error(e)
					return
				}
			}
		}()
	}
	wg.Wait()
}

func TestBatchParityAndValidation(t *testing.T) {
	r := realRequest(t)
	b, e := NewBatch([]Request{r, r})
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	if e = b.Solve(); e != nil {
		t.Fatal(e)
	}
	var scalar Solver
	if e = scalar.Solve(&r); e != nil {
		t.Fatal(e)
	}
	for _, v := range b.Results() {
		for i, x := range v.Factor {
			if math.Float64bits(x) != math.Float64bits(scalar.Factor[i]) {
				t.Fatal("batch differs")
			}
		}
	}
	// Array mutations are revalidated on every call, not just at preparation.
	original := r.Knots[1]
	r.Knots[1] = r.Knots[0]
	if b.Solve() == nil {
		t.Fatal("batch skipped validation")
	}
	r.Knots[1] = original
	if e = b.Solve(); e != nil {
		t.Fatal(e)
	}
	if n := testing.AllocsPerRun(100, func() {
		if e := b.Solve(); e != nil {
			panic(e)
		}
	}); n != 0 {
		t.Fatalf("batch allocated %v", n)
	}
	b.Close()
	if b.Solve() == nil {
		t.Fatal("used closed batch")
	}
}
func TestBatchEmpty(t *testing.T) {
	if _, e := NewBatch(nil); e == nil {
		t.Fatal("accepted empty batch")
	}
	var b Batch
	if b.Solve() == nil {
		t.Fatal("accepted zero value batch")
	}
}
