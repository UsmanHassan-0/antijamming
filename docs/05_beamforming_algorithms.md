# Beamforming Algorithms

The runtime convention is `y[n] = w^H x[n]`, implemented as
`w.conj() @ samples`. Uniform sum uses `w = [1, 1, 1, 1]`; it remains
the off/arming/release safety target, not a selectable LCMV algorithm.

## One measured-vector solve

The only product nulling method is `covariance_lcmv_measured_u1`:

```text
R_loaded = R + delta I
delta = loading_abs + loading_rel * trace(R).real / N
C = [normalized_frozen_healthy_vector, normalized_current_u1]
w = R_loaded^-1 C (C^H R_loaded^-1 C)^-1 f
```

The constraints preserve the uniform combiner's complex response to the frozen
healthy vector and force zero response to current measured U1. The code checks
`C^H w ~= f`, conditioning, finite values, and weight norm. The ideal-angle
null solver, its public export, runtime candidate, and method ranking are
removed. No selector or compatibility alias recreates them.

One accepted measured-U1 solve supplies both the common target and the shared
GNSS protection target. Common output still has its existing transition;
assigned PRNs may receive the shared row times a complex continuity scalar.
Unassigned slots use the common row. This is not an independent LCMV solve per
PRN, and a base-row model plot is not the final compensated FIFO response.

## Activation, preservation, and release

The runtime learns a stable jammer-off bladeRF angle cluster and healthy
measured vector/covariance. Enabling LCMV freezes that reference. MUSIC still
supplies a candidate outside the frozen desired-direction guard; missing or
inside-guard candidates prevent a fresh solve. This retained control dependency
does not turn the MUSIC angle into the measured null vector.

Activation requires both input-power rise and generalized-covariance rise
against the frozen baseline (profile: 3 and 6 dB). Cleanup keeps historical
detection separate from currently active protection. With valid evidence,
protection releases after both lower thresholds (1.5 and 3 dB) hold for two
seconds; returning to uniform includes the existing phase-bank transition.
See the living [jammer audit](audits/jammer_latch_audit_2026-09-01.md)
for exercised schedules and unverified boundaries.

The healthy-vector equality is not a proof of preservation for every PRN.
Desired-loss and noise-gain metrics remain diagnostics; current preflight does
not reject solely on white-noise gain. Excessive weight norm, invalid
constraints, or unavailable solutions reject a new update. A common uniform
fallback is not by itself proof that every FIFO is uniform: while protection
is active the bank can retain a previously accepted protection row.

## MUSIC and LCMV graph semantics

MUSIC/Bartlett still use the antenna steering model for their angle scans.
The LCMV graph has exactly one curve:
**Measured-U1 target weights (model)**. It scans the accepted measured-U1
target against that antenna model; the x-axis is display bearing and the
y-axis is the absolute, unnormalized calculated response in dB.

The vertical line is explicitly a **MUSIC guard candidate**, not a measured
jammer bearing or the measured-U1 null direction. U1 need not correspond to
one physical bearing. Obsolete runtime null-angle fields are removed rather
than being filled from MUSIC or left empty. The graph does not show PRN scalars, intermediate
phase-bank rows, or measured OTA suppression.

A very deep calculated minimum is not a physical suppression measurement.
The scalar compensation and transition failures in historical audits are not
claimed repaired by deleting the ideal solver. Receiver C/N0, tracking, PVT,
and final FIFO output still require matched hardware validation.

## Research boundary

Measured interference subspaces avoid deriving a null solely from an assumed
angle/manifold, but dominant U1 is not automatically a jammer. A strong desired
bladeRF signal can dominate; multiple interference modes can require a larger
subspace. These are limitations, not newly implemented alternatives.

The GNSS subspace and distortionless-filter literature motivates measured
constraints plus desired-signal protection, but is not an implementation or
benchmark of this code:
[GNSS space-time interference mitigation, Sections 3.1–3.2](https://pmc.ncbi.nlm.nih.gov/articles/PMC4507627/).
The current implementation is spatial-only, not that paper's space-time filter.
