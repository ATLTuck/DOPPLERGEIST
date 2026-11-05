// Linux offline batch/benchmark driver: no network or geolocation.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	acoustic "github.com/ATLTuck/DOPPLERGEIST/native/acoustic"
	"io"
	"os"
	"runtime"
	"syscall"
	"time"
)

func run() error {
	input := flag.String("input", "", "JSON request array")
	repeat := flag.Int("repeat", 1, "repetitions excluding parsing/output")
	flag.Parse()
	if *input == "" || *repeat < 1 || *repeat > 1000000 {
		return fmt.Errorf("require -input and repeat 1..1000000")
	}
	f, err := os.Open(*input)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() > 64<<20 {
		return fmt.Errorf("input must be a regular file <= 64 MiB")
	}
	dec := json.NewDecoder(io.LimitReader(f, 64<<20))
	dec.DisallowUnknownFields()
	var requests []acoustic.Request
	if err = dec.Decode(&requests); err != nil {
		return err
	}
	var extra any
	if dec.Decode(&extra) != io.EOF {
		return fmt.Errorf("trailing or oversized input")
	}
	if len(requests) < 1 || len(requests) > 4096 {
		return fmt.Errorf("require 1..4096 requests")
	}
	var solver acoustic.Solver
	for i := range requests {
		if err = solver.Solve(&requests[i]); err != nil {
			return fmt.Errorf("request %d: %w", i, err)
		}
	}
	var a, b syscall.Rusage
	if err = syscall.Getrusage(syscall.RUSAGE_SELF, &a); err != nil {
		return err
	}
	start := time.Now()
	for j := 0; j < *repeat; j++ {
		for i := range requests {
			if err = solver.Solve(&requests[i]); err != nil {
				return err
			}
		}
	}
	wall := time.Since(start).Seconds()
	if err = syscall.Getrusage(syscall.RUSAGE_SELF, &b); err != nil {
		return err
	}
	cpu := func(r syscall.Rusage) float64 {
		return float64(r.Utime.Sec+r.Stime.Sec) + float64(r.Utime.Usec+r.Stime.Usec)/1e6
	}
	// Serialization is deliberately outside the solver timer. Return every request,
	// not just the final batch element; unsupported outputs remain explicit nulls.
	results := make([]map[string]any, 0, len(requests))
	for i := range requests {
		if err = solver.Solve(&requests[i]); err != nil {
			return err
		}
		results = append(results, map[string]any{"factor": acoustic.JSONValues(solver.Factor), "emission": acoustic.JSONValues(solver.Emission), "distance": acoustic.JSONValues(solver.Distance)})
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"requests": len(requests), "repeats": *repeat, "wall_seconds": wall, "cpu_seconds": cpu(b) - cpu(a), "peak_rss_kib": b.Maxrss, "go_version": runtime.Version(), "results": results})
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
