# Beamforming Algorithms

The runtime beamformer convention is:

```text
y[n] = w^H x[n]
```

In Python this is `w.conj() @ samples`. For a spatial vector `v`, response is `w^H v`.

Uniform sum uses `w = [1, 1, 1, 1]` and preserves the old raw four-channel sum into one GNSS-SDR stream. Uniform average would use `[0.25, 0.25, 0.25, 0.25]`; this is useful as a noise-gain reference but is not the default FIFO combiner.

The product has one LCMV implementation. It uses diagonal-loaded covariance:

```text
R_loaded = R + delta I
delta = lcmv_covariance_diagonal_loading_abs
      + lcmv_covariance_diagonal_loading_rel * trace(R).real / N

w = R_loaded^-1 C (C^H R_loaded^-1 C)^-1 f
```

All constraints are checked as `C^H w ~= f`.

The two selectable methods are thin constraint-vector choices around that same
covariance solver:

- `covariance_lcmv_ideal`: product method; full covariance LCMV with the ideal steering vector at the MUSIC internal angle as its null constraint.
- `covariance_lcmv_measured_u1`: full covariance LCMV with measured `u1` null.

The current product config uses `lcmv_test_null_method: "covariance_lcmv_ideal"`, so the jammer null uses the ideal steering vector. The preserve constraint is separate: `lcmv_preserve_constraint_mode: "realtime_bladerf_measured_u1"` tracks a stable jammer-off bladeRF angle cluster, verifies that the recent healthy-reference angle belongs to that cluster, and freezes the measured healthy U1 and covariance when the operator enables LCMV. The old ideal-angle-only bladeRF preserve mode was removed after the 2026-08-07 live test showed 7-9 dB of C/N0 loss despite near-zero mathematical constraint residuals.

Enabling LCMV first arms the runtime while the FIFO remains on uniform weights. Angular movement alone cannot activate covariance weights. Activation requires both an input-power rise and a generalized covariance-mode rise against the exact frozen arm-time baseline. Detection then latches until LCMV is disabled, so a long jammer interval cannot silently return the system to unprotected uniform weights.

The legacy single-FIFO path used when Shared-U1 fanout is disabled applies target weights with a one-second
complex linear chunk ramp. The uniform and LCMV endpoints have the same complex
response to the frozen measured bladeRF U1, so every interpolated weight has
that same response. Repeated covariance updates do not restart an active ramp;
the current ramp finishes before a later target can be scheduled.

The optional shared measured-U1 GNSS fanout is deliberately different. It
publishes every newly accepted measured-U1 covariance solution immediately so
the spatial null can follow the live covariance. Before applying that row to a
PRN, it multiplies the row by the exact complex scalar that keeps that PRN's
previous response unchanged. Jammer-off recovery to the common/uniform row is
the configured one-second ramp, and its two endpoints have the same complex
response, so every intermediate chunk has the same response too. The fanout is
one shared spatial beam followed by PRN-specific complex scalars; it contains
no per-PRN covariance or LCMV solver.

When this fanout is enabled, the GNSS FIFO protection row comes from the
accepted `covariance_lcmv_measured_u1` candidate even if the ordinary active
method displayed in the GUI is `covariance_lcmv_ideal`. The status log therefore
records the FIFO path independently from the ordinary active-method label.

There is no desired-loss guard or desired-loss configuration threshold in the
product path. The frozen measured bladeRF U1 is an explicit equality
constraint, while desired/SOI loss versus the healthy reference remains a
diagnostic only. Invalid/non-finite weights, excessive weight norm, unavailable
covariance solutions, protected bladeRF bearings, missing/stale measured
references, and absent jammer activation evidence retain uniform fallbacks.

Covariance-free constraint projection, the old ideal-steering shortcut, and the old ideal-angle fan have been removed from the product module and tests. The runtime schema rejects their old method names.

Common live signal model:

```text
x[n] = desired/SOI + jammer + real sky GNSS + noise + multipath + receiver artifacts
R = E{x x^H}
```

`R` is not jammer-only. `u1` is not always jammer. When the jammer is off, `u1` can be healthy/SOI/bladeRF-like. When the jammer is on and strong, `u1` can become jammer-like. SOI inside `R` can be damaged unless protected by a correct desired constraint. GSC is a future-work path for separating blocking/nulling from an adaptive noise canceller.

The measured-u1 method is not the product default because `u1` is not always the jammer. Its desired/SOI loss must be measured against a reliable run-local healthy reference before an operator selects it.
