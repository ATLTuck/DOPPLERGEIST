/* Derived from DOPPLERGEIST _native_acoustic.c, Copyright 2026 Cinna Davis.
 * PolyForm Noncommercial 1.0.0; see ../../LICENSE. */
#include "core.h"
/* Optional exact-order acoustic solver for canonical float64 buffers.
 * NumPy/SciPy remain the reference path for unsupported representations and
 * all exceptional arithmetic. No NumPy C headers or runtime compilation.
 */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <fenv.h>
#include <string.h>

/* Larger calls keep the original all-lane solver instead of allocating
 * unbounded temporary state or falling back to the Python implementation. */
#define DG_MAX_STATE_BYTES ((size_t)2 * 1024 * 1024)

/* Compile without contraction or reassociation.  SciPy PPoly uses
 * increasing powers, not Horner evaluation.  Preserve that arithmetic exactly.
 */
static size_t interval_at(const double *x, size_t m, double value) {
    size_t lo = 0, hi = m - 1;
    if (value == x[m]) return m - 1;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (value < x[mid + 1]) hi = mid;
        else lo = mid + 1;
    }
    return lo;
}

static int evaluate(const double *coefficients, const double *x, size_t m,
                    size_t width, double origin, double t, double *values,
                    uint16_t *interval) {
    double relative = t - origin;
    if (!(relative >= x[0] && relative <= x[m])) return 1;
    size_t i = interval == NULL ? m : *interval;
    /* Newton updates usually stay in one native spline interval. Reuse only
     * after checking its exact half-open bounds; the last interval owns x[m].
     * Both spline shapes are bounded to 4096 intervals by the public wrapper.
     */
    if (!(i < m && relative >= x[i] &&
          (relative < x[i+1] || (i+1 == m && relative == x[m])))) {
        i = interval_at(x, m, relative);
        if (interval != NULL) *interval = (uint16_t)i;
    }
    double s = relative - x[i];
    double z = 1.0;
    for (size_t j = 0; j < width; ++j) values[j] = 0.0;
    for (size_t k = 0; k < 4; ++k) {
        for (size_t j = 0; j < width; ++j)
            values[j] = values[j] + coefficients[((3-k)*m+i)*width+j]*z;
        if (k < 3) z *= s;
    }
    return 0;
}



static double clip(double t, double start, double end) {
    if (t < start) return start;
    if (t > end) return end;
    return t;
}

static double norm3(const double *a) {
    return sqrt((a[0]*a[0] + a[1]*a[1]) + a[2]*a[2]);
}

static int kinematics(const double *coefficients, const double *x,
                      size_t m, double origin, double tau, const double *observer,
                      double *distance, double *radial, uint16_t *interval) {
    double values[6], delta[3];
    if (evaluate(coefficients, x, m, 6, origin, tau, values, interval)) return 1;
    for (size_t j = 0; j < 3; ++j) delta[j] = values[j] - observer[j];
    *distance = norm3(delta);
    *radial = (((0.0 + delta[0]*values[3]) + delta[1]*values[4])
               + delta[2]*values[5]) / *distance;
    return !isfinite(*distance) || !isfinite(*radial) || *distance == 0.0;
}

static int dg_acoustic(const double *t, size_t n, const double *observer,
                const double *p, const double *x, size_t m,
                const double *packed, const double *packed_x, size_t packed_m,
                double origin, double start, double end, double c,
                double arrival_start, double arrival_end,
                double *factor, double *tau, double *distance, uint16_t *state) {
    if (n == 0 || m == 0 || m > 4096) return 1;
    uint16_t initial_interval = (uint16_t)m;
    for (size_t i = 0; i < n; ++i) {
        double position[3], delta[3];
        if (!isfinite(t[i])) return 1;
        if (evaluate(p, x, m, 3, origin, clip(t[i], start, end), position,
                     &initial_interval)) return 1;
        for (size_t j = 0; j < 3; ++j) delta[j] = position[j] - observer[j];
        double initial_distance = norm3(delta);
        if (!isfinite(initial_distance)) return 1;
        double initial_delay = initial_distance/c;
        double estimate = t[i] - initial_delay;
        if (!isfinite(initial_delay) || !isfinite(estimate)) return 1;
        tau[i] = clip(estimate, start, end);
        if (state != NULL) state[i] = (uint16_t)packed_m;
    }
    int unchanged = 0;
    double fixed_maximum = 0.0;
    for (size_t iteration = 0; iteration < 10; ++iteration) {
        unchanged = 1;
        double maximum = fixed_maximum;
        for (size_t i = 0; i < n; ++i) {
            /* UINT16_MAX marks an exact floating-point fixed point. Repeating
             * its polynomial evaluation and update cannot change its values.
             * Its correction still participates in the global stopping rule:
             * a clamped unsupported endpoint can have a large correction.
             */
            if (state != NULL && state[i] == UINT16_MAX) continue;
            if (kinematics(packed, packed_x, packed_m, origin, tau[i], observer,
                           distance+i, factor+i, state == NULL ? NULL : state+i)) return 1;
            double derivative = 1.0 + factor[i]/c;
            if (!isfinite(derivative) || !(derivative > 0.0)) return 1;
            double correction = ((tau[i] - t[i]) + distance[i]/c)/derivative;
            if (!isfinite(correction)) return 1;
            double estimate = tau[i] - correction;
            if (!isfinite(estimate)) return 1;
            double updated = clip(estimate, start, end);
            if (updated != tau[i]) unchanged = 0;
            else if (state != NULL && (updated != 0.0 || signbit(updated) == signbit(tau[i]))) {
                /* A signed-zero change compares equal but need not reproduce
                 * the same polynomial arithmetic on a subsequent iteration. */
                state[i] = UINT16_MAX;
                if (fabs(correction) > fixed_maximum) fixed_maximum = fabs(correction);
            }
            tau[i] = updated;
            double absolute = fabs(correction);
            if (absolute > maximum) maximum = absolute;
        }
        if (maximum < 1e-7 || unchanged) break;
    }
    if (!unchanged) {
        for (size_t i = 0; i < n; ++i) {
            if (state != NULL && state[i] == UINT16_MAX) continue;
            if (kinematics(packed, packed_x, packed_m, origin, tau[i], observer,
                           distance+i, factor+i, state == NULL ? NULL : state+i)) return 1;
        }
    }
    for (size_t i = 0; i < n; ++i) {
        double radial = factor[i];
        double denominator = c+radial;
        if (!isfinite(denominator)) return 1;
        factor[i] = c/denominator;
        double derivative = 1.0 + radial/c;
        if (!isfinite(factor[i]) || !isfinite(derivative) || !(derivative > 0.0)) return 1;
        double residual = ((tau[i]-t[i])+distance[i]/c)/derivative;
        if (!isfinite(residual)) return 1;
        int supported = t[i] >= arrival_start && t[i] <= arrival_end;
        double spacing = nextafter(t[i],copysign(INFINITY,t[i])) - t[i];
        double tolerance = fmax(1e-7,4.0*fabs(spacing));
        if (!isfinite(tolerance)) return 1;
        if (supported && fabs(residual) > tolerance) return 1;
        if (!supported) factor[i] = tau[i] = distance[i] = NAN;
    }
    return 0;
}

/* A checked, allocation-free ABI; caller owns all buffers and workspace. */
int dg_solve(const double *t, size_t n, const double *observer,
 const double *p, size_t plen, const double *x, size_t m,
 const double *packed, size_t packed_len, const double *packed_x, size_t pm,
 double origin, double start, double end, double c,
 double arrival_start, double arrival_end,
 double *factor, double *tau, double *distance, uint16_t *state, size_t state_len) {
 if (!t || !observer || !p || !x || !packed || !packed_x ||
     !factor || !tau || !distance || !state || n == 0 || state_len < n ||
     m == 0 || m > 4096 || pm == 0 || pm > 4096 ||
     plen != 12*m || packed_len != 24*pm ||
     !isfinite(origin) || !isfinite(start) || !isfinite(end) || start > end ||
     !isfinite(c) || c <= 0 || !isfinite(arrival_start) ||
     !isfinite(arrival_end) || arrival_start > arrival_end ||
     fegetround() != FE_TONEAREST) return DG_INVALID;
 for (size_t i=0; i<3; ++i) if (!isfinite(observer[i])) return DG_INVALID;
 for (size_t i=0; i<=m; ++i)
   if (!isfinite(x[i]) || (i && !(x[i-1] < x[i]))) return DG_INVALID;
 for (size_t i=0; i<=pm; ++i)
   if (!isfinite(packed_x[i]) || (i && !(packed_x[i-1] < packed_x[i]))) return DG_INVALID;
 fenv_t previous;
 if (fegetenv(&previous)) return DG_INVALID;
 int result = dg_acoustic(t,n,observer,p,x,m,packed,packed_x,pm,
   origin,start,end,c,arrival_start,arrival_end,factor,tau,distance,state);
 if (fesetenv(&previous)) return DG_INVALID;
 return result ? DG_FALLBACK : DG_OK;
}

int dg_solve_batch(dg_job *jobs, size_t count) {
 if (!jobs || !count || count > 4096) return DG_INVALID;
 int failed = 0;
 for (size_t i=0; i<count; ++i) {
  dg_job *j = jobs+i;
  j->result = dg_solve(j->t,j->n,j->observer,j->p,j->plen,j->x,j->m,
   j->packed,j->packed_len,j->packed_x,j->pm,j->origin,j->start,j->end,
   j->c,j->arrival_start,j->arrival_end,j->factor,j->tau,j->distance,
   j->state,j->state_len);
  if (j->result != DG_OK) failed = 1;
 }
 return failed ? DG_FALLBACK : DG_OK;
}
