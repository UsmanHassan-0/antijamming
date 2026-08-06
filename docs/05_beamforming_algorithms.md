# Beamforming Algorithms

The runtime beamformer convention is:

```text
y[n] = w^H x[n]
```

In Python this is `w.conj() @ samples`. For a spatial vector `v`, response is `w^H v`.

Uniform sum uses `w = [1, 1, 1, 1]` and preserves the old raw four-channel sum into one GNSS-SDR stream. Uniform average would use `[0.25, 0.25, 0.25, 0.25]`; this is useful as a noise-gain reference but is not the default FIFO combiner.

Constraint-only LCMV uses:

```text
w = C (C^H C)^-1 f
```

Full covariance LCMV uses diagonal-loaded covariance:

```text
R_loaded = R + delta I
delta = lcmv_covariance_diagonal_loading_abs
      + lcmv_covariance_diagonal_loading_rel * trace(R).real / N

w = R_loaded^-1 C (C^H R_loaded^-1 C)^-1 f
```

All constraints are checked as `C^H w ~= f`.

Current non-legacy methods:

- `covariance_lcmv_ideal`: product method; full covariance LCMV with the ideal steering vector at the MUSIC internal angle as its null constraint.
- `measured_dominant_eigenvector`: diagnostic constraint-only null with `[ones, u1_norm]`.
- `covariance_lcmv_measured_u1`: full covariance LCMV with measured `u1` null.

The current product config uses `lcmv_test_null_method: "covariance_lcmv_ideal"`, so the active FIFO weights are the full-covariance ideal-steering LCMV weights when LCMV test mode is on and valid. Measured-u1 methods remain candidate diagnostics unless explicitly selected in the config.

The product profile sets `lcmv_desired_loss_guard_enabled: false`. Desired/SOI loss versus the healthy reference and the 6 dB diagnostic threshold remain in the logs, but exceeding that threshold does not reject the active LCMV method or switch the FIFO to uniform summation. Invalid/non-finite weights, excessive weight norm, unavailable covariance solutions, protected bladeRF bearings, and insufficient predicted target suppression retain their existing fallbacks.

The old constraint-only ideal-steering null and ideal-angle fan remain unit-test helpers named `legacy_constraint_null_ideal_weights` and `legacy_angle_fan_diagnostic_weights`. They are not accepted runtime active-method values, are not computed as live candidates, and are not ranked by the run summary.

Common live signal model:

```text
x[n] = desired/SOI + jammer + real sky GNSS + noise + multipath + receiver artifacts
R = E{x x^H}
```

`R` is not jammer-only. `u1` is not always jammer. When the jammer is off, `u1` can be healthy/SOI/bladeRF-like. When the jammer is on and strong, `u1` can become jammer-like. SOI inside `R` can be damaged unless protected by a correct desired constraint. GSC is a future-work path for separating blocking/nulling from an adaptive noise canceller.

Measured-u1 methods are not the product default because `u1` is not always the jammer. Their desired/SOI loss must be measured against a reliable run-local healthy reference before an operator selects either measured method.
