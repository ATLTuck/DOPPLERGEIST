/* Copyright 2026 Cinna Davis; PolyForm Noncommercial 1.0.0. */
#ifndef DG_ACOUSTIC_H
#define DG_ACOUSTIC_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { DG_OK=0, DG_INVALID=1, DG_FALLBACK=2 };
/* Contiguous doubles, SciPy PPoly order [4][intervals][3 or 6].
 * x and packed_x have m+1 and pm+1 elements. All buffers must be valid,
 * aligned and nonoverlapping. Outputs are undefined unless DG_OK.
 * state has n uint16_t entries. No allocation/global mutable state.
 * Finite ordered arrival bounds are mandatory. Motion-support validation
 * remains the caller's responsibility, as in the Python native adapter. */
int dg_solve(const double*,size_t,const double*,
 const double*,size_t,const double*,size_t,
 const double*,size_t,const double*,size_t,
 double,double,double,double,double,double,
 double*,double*,double*,uint16_t*,size_t);
/* Prepared descriptors amortize language dispatch, NOT validation. Pointers and
 * lengths obey dg_solve's contract. Every job gets its own result status.
 * Writable job buffers/workspaces must not alias one another. */
typedef struct dg_job {
 const double *t; size_t n; const double *observer;
 const double *p; size_t plen; const double *x; size_t m;
 const double *packed; size_t packed_len; const double *packed_x; size_t pm;
 double origin,start,end,c,arrival_start,arrival_end;
 double *factor,*tau,*distance; uint16_t *state; size_t state_len;
 int result;
} dg_job;
int dg_solve_batch(dg_job *jobs, size_t count);
#ifdef __cplusplus
}
#endif
#endif
