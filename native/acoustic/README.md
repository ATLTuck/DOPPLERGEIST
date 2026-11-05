# Experimental standalone acoustic core + Go driver

This is a **first migration slice**, not a Go/C++ rewrite of the application.
The production Python pipeline and native extension are unchanged. It solves
emission time, Doppler factor and distance for supplied trajectory splines.
It does not discover aircraft/audio, acquire ADS-B, validate trajectory motion,
search locations, or produce calibrated confidence.

## What ships

- `core.h` / `core.c`: narrow C ABI, derived from the existing native solver;
  same polynomial evaluation order, global ten-step Newton stop, fixed-point
  handling, support boundaries, NaNs, and residual checks. Caller-provided
  workspace and outputs eliminate per-call workspace allocation. No Python API,
  dependencies beyond libc/libm, or global mutable solver state.
- `acoustic.go`: cgo adapter with shape/sample-budget checks and reusable buffers.
  Successful results alias the solver buffers until the next call. One solver per
  goroutine; do not alias request arrays with outputs. Public output slices must
  not be modified concurrently with Solve. Failed calls leave invalid outputs.
- `cmd/dg-acoustic`: Linux offline JSON batch CLI with bounded input (64 MiB),
  max 4096 requests / 1,048,576 samples per request, all batch results, and
  solver CPU/wall timing plus process peak RSS. Unsupported output is JSON null.
- Real archived trajectory differential tests, golden Go checks, allocation test,
  native-C baseline measurement harness, and a separate Linux CI workflow.

C++ compilation **passed on the initial commit in GitHub CI**, but is not locally verified: the box
had GCC but no C++ compiler. Signed Debian HTTP metadata returned HTTP 500;
HTTPS apt transport failed TLS. No signature or TLS checks were disabled.
The tested delivery uses C + Go, as the explicitly authorized fallback. The C ABI
is ready for C++ callers; this is not a separately optimized C++ algorithm.

## Build and run (Linux)

Prerequisites: Go >=1.23, GCC, Python >=3.11 for reference checks. No Go modules
are downloaded. cgo's flag allowlist needs the *specific* strict FP flag below;
never use `CGO_CFLAGS_ALLOW=.*` or fast-math.

```sh
cd native/acoustic
export CGO_CFLAGS_ALLOW='-ffp-contract=off'
go test -race ./...
go build -o /tmp/dg-acoustic ./cmd/dg-acoustic
/tmp/dg-acoustic -input real-request.json -repeat 1000000
go test -bench . -benchmem -count 3 ./...
# Independently usable shared library, no Python headers needed:
gcc -O3 -ffp-contract=off -fno-fast-math -shared -fPIC core.c -lm -o /tmp/libdg_acoustic.so
```

The JSON schema is the exported `Request` structure in `acoustic.go`. Position
coefficients flatten SciPy PPoly `[4][intervals][3]` in C order; packed kinematics
use `[4][intervals][6]`. Knots are relative to origin, receive times are absolute,
and observer/position coordinates are ECEF metres. Sound speed is metres/second.
Input must provide finite ordered scalar bounds; NaN/Infinity input is not JSON.
The C ABI requires valid aligned nonoverlapping buffers of documented lengths.
It returns 0 on success, 1 for invalid inputs, 2 when a reference fallback is
required. It restores the floating-point environment. It never extrapolates.
Unlike the Python adapter it does **not** validate Track motion-support evidence:
callers must do so before using its predictions scientifically. The CLI fails
explicitly rather than silently inventing a result when fallback is required.

`real-request.json` is an exported real observation spline, not generated flight
or audio data. Its source and flight ID are in `real-request.expected.json`.
The receiver is an evaluation point below the first observed trajectory sample,
**not microphone ground truth**. The file checks software arithmetic only.

## Differential tests / reproducible measurements

From repository root, with uv installed (or use an ordinary venv and pip):

```sh
uv venv --python 3.12 .venv-reference
uv pip install --python .venv-reference/bin/python numpy scipy pandas pyproj pytest setuptools
.venv-reference/bin/python setup.py build_ext --inplace
export PYTHONPATH=src DG_ACOUSTIC_BIN=/tmp/dg-acoustic
.venv-reference/bin/python native/acoustic/tools/verify.py
.venv-reference/bin/python -m pytest -q tests/test_real_native_acoustic.py
.venv-reference/bin/python native/acoustic/tools/verify.py --benchmark 20000
# Regenerate the small golden request from the archived real observations:
.venv-reference/bin/python native/acoustic/tools/verify.py --export native/acoustic/real-request.json
```

`verify.py` checks four supported archived trajectory segments, bitwise native C
and Python reference agreement, boundaries/adjacent floats, reversed queries,
sound speeds, signed-zero queries, invalid knots/parameters, NaN/Infinity
fallback, extreme sound speed, empty input, workspace sizing, and CLI batch/error
behavior. It is not a real-recording accuracy test. Existing upstream checks also
retain policy/layout/method-override fallback and mutable-cache behavior.

### Local measurements (November 5, 2026, EST)

Shared Linux x86-64 Intel Xeon host; GCC 14.2, Go 1.27.3, Python 3.12.15,
NumPy 2.5.4, SciPy 1.18.1. No CPU affinity/isolation; hot caches. Treat short
microbenchmarks as noisy, not application speedups.

Three alternating pairs, 20,000 repetitions of four archived trajectory queries
(203 total receive samples per repetition), preallocated inputs/outputs:

| Invocation | Median wall seconds | Median CPU seconds | Process peak RSS KiB |
|---|---:|---:|---:|
| Existing CPython native C extension | 0.448206 | 0.448126 | 136124 |
| Standalone ABI through Python ctypes | 0.618900 | 0.618881 | 136124 |

The standalone ctypes route is approximately **38% slower** on this workload.
The arithmetic kernel is intentionally unchanged; differing Python dispatch and
validation costs are included. Both run in the same process, so cumulative peak
RSS is **not** attributable to either backend. No production default changed.

Go's five-sample real golden request: three `go test -bench` observations were
511.1, 522.2, 508.9 ns/op, each 0 B/op and 0 Go allocations/op. A standalone CLI
run with 1,000,000 repetitions measured 0.506863 s solver wall, 0.511399 s CPU,
5220 KiB process peak RSS. Parsing, warm-up and serialization are outside its
solver timer, but included in lifetime RSS. This tiny request is **not comparable**
to the four-trajectory Python benchmark; it does not demonstrate kernel or
application acceleration. Workspace reuse and removal of the Python requirement
are the delivered practical improvements.

## Limits and next slice

No end-to-end localization run or accuracy claim; full discovery, acquisition,
indexing, scoring/refinement, scientific support decisions, UI, jobs and resume
remain Python. Wider adoption should profile real search workloads first and
use a versioned shared kernel to avoid maintaining duplicate arithmetic. Do not
replace the reference implementation or introduce fast-math based on this slice.
C++ local testing, non-Linux platform testing, and full application benchmarks
remain outstanding. CI results must be checked separately after publication.

## License / attribution

Derived from `src/dopplergeist/_native_acoustic.c` at upstream commit
`7d74fc2d6f4be4a1df747a13ded0fabeb9a31148`.
Required Notice: Copyright 2026 Cinna Davis.
[PolyForm Noncommercial 1.0.0](../../LICENSE) applies; all upstream notices,
legacy MIT grants, and third-party/data terms remain intact. A language port does
not grant commercial rights. No REA code or dependency is used.

## Performance follow-up: batch boundary, not a faster algorithm

`dg_solve_batch` accepts up to 4096 prepared descriptors in one call. Every job
still calls the complete validated `dg_solve`; errors are recorded per job,
valid jobs still execute, and numerical operation order is unchanged. Writable
outputs/workspaces must not overlap. Go's `NewBatch` pins request/output buffers
using `runtime.Pinner`; always defer `Close`. Requests/scalars are captured at
construction, array elements can be changed between calls and are revalidated.
It is not safe to resize or concurrently mutate captured arrays. `Results` aliases
owned buffers. Batch lifetime memory is larger than the reusable scalar solver.

```sh
# Root: export the IDENTICAL four-trajectory / 203-sample workload for Go.
PYTHONPATH=src .venv-reference/bin/python native/acoustic/tools/verify.py --export-batch /tmp/real-batch.json
/tmp/dg-acoustic -input /tmp/real-batch.json -repeat 100000 -dispatch scalar
/tmp/dg-acoustic -input /tmp/real-batch.json -repeat 100000 -dispatch batch
# Root: boundary comparison and equivalent-amortized-dispatch control.
PYTHONPATH=src .venv-reference/bin/python native/acoustic/tools/verify.py --benchmark 20000
PYTHONPATH=src .venv-reference/bin/python native/acoustic/tools/profile.py
```

Follow-up measurements, same shared host/dependencies, three alternating runs:

| Python invocation (20,000 repetitions, same 4 trajectories) | Median wall s | Median CPU s |
|---|---:|---:|
| Existing native C extension, four calls/sweep | 0.442406 | 0.442383 |
| Standalone ctypes scalar, four calls/sweep | 0.613749 | 0.613706 |
| Standalone ctypes batch, one call/sweep | 0.439500 | 0.439474 |

Batching reduces this ctypes adapter's elapsed time by **28.4%**, eliminating the
observed integration regression; it is **not demonstrably faster than the existing
extension**. The 0.7% median difference versus that extension is too small/noisy
to call a win (batch run range 0.437049–0.464410 s). Cumulative same-process peak
RSS was 136564 KiB for all three routes, not backend-specific memory.

`profile.py` compiles the original unchanged C arithmetic into a diagnostic
baseline with the same checked C ABI validation and original stack/heap workspace
threshold. Its heap allocator is libc malloc/free, **not CPython PyMem**. It verifies
all output bits before timing, then replays entire loops inside C so each mode
crosses ctypes **once per 100,000 repetitions**, excluding Python buffer parsing,
ctypes per-job dispatch and descriptor construction for every mode:

| Matched C-loop control (100,000 repetitions) | Median wall s | Median CPU s |
|---|---:|---:|
| Original arithmetic + stack/heap workspace policy | 2.198448 | 2.198255 |
| Standalone batched ABI | 2.198060 | 2.193698 |
| Standalone scalar ABI, loop inside C | 2.156105 | 2.155992 |

Cumulative same-process peak RSS: 136284 KiB. One standalone scalar observation
was 2.561229 s; all runs are retained by the script. These controls show **no
convincing algorithm/workspace speedup**. Scaling their time to 20,000 repetitions
puts native computation/validation near 0.44 s: most of the *extra* 0.17 s of the
original ctypes result comes from scalar language dispatch, not slower arithmetic.
No validation or physics checks were removed for these measurements.

Identical four-trajectory Go CLI workload, 100,000 repetitions, three alternating
fresh processes: scalar median wall/CPU 2.235273/2.275683 s; batch
2.270839/2.301991 s (about 1.6% slower wall). Median lifetime RSS was 12628 KiB for
both. An earlier adapter build with unnecessary batch preparation in scalar mode
showed a 5.2% batch slowdown; neither measurement demonstrates an improvement.
Pinning/batch preparation, JSON and output are excluded from solver timing,
but lifetime RSS includes them. **Scalar remains the CLI default**; batch is opt-in
for integrations where amortizing a higher-overhead boundary demonstrably helps.
No claim that cgo batching accelerates this workload.

Follow-up checks: 14 Python native/reference/batch/CLI tests and 8 Go tests,
including pinned batch lifecycle, per-call validation after input mutation,
independent job status, bitwise output parity and zero warm batch Go allocations.
The initial commit's full CI passed (including C++ compilation, Python 3.11/3.14
installation/static checks, sdist and Docker). The follow-up adds C++-compiled
library differential execution to CI; its outcome must be checked separately.

Next useful migration boundary: caller-prepared candidate-scoring batches, only
after profiling real search/refinement workloads. This pass justifies avoiding
per-trajectory ctypes calls, **not** replacing the scientific stack or claiming
an end-to-end win. Production pipeline/defaults remain unchanged.
