# Diagnostics And Metrics

The backend writes candidate diagnostics once into `spatial_vector_diagnostics.jsonl`. Full LCMV response JSON is written once into `lcmv_pattern_absolute.jsonl`; both are throttled by `lcmv_heavy_diagnostics_interval_s`, while concise LCMV status can still update more often. `analysis.log` retains the full-angle MUSIC/Bartlett snapshots without duplicating those dedicated records. `tools/summarize_lcmv_run.py` reads the current dedicated files. Obsolete experiment-manifest, expected-bearing, and RF-budget formats are not accepted by the active analysis path.

Only `covariance_lcmv_measured_u1` is a live nulling method. One solve supplies
both common and GNSS protection targets. The ideal-null solver and its
comparison/ranking metrics are removed; steering-model comparisons remain
diagnostics, not an alternate LCMV implementation.

Important metrics:

- Ideal steering suppression: predicted reduction of the ideal steering component.
- U1 measured component suppression: predicted reduction of the dominant measured covariance eigenvector.
- Total output reduction: measured or covariance-predicted drop in total output power. This is not jammer-only suppression.
- Added-scene suppression: the runtime PSD-projects `R_current - R_arm`, then applies the uniform and actually applied weights to that same covariance. This avoids mixing desired-signal loss into the numerator merely because the weights changed. The automatic activation latch is only a safety gate, not proof that the added scene is the physical jammer.
- Jammer-only suppression: the summary accepts the runtime same-covariance result as jammer-only only inside an explicit operator `jammer_on` marker window with the bladeRF scene otherwise unchanged.
- Desired loss: response loss versus the healthy reference vector, when available.
- Noise gain: weight power, usually `10log10(||w||^2)` or relative to a reference.
- Effective J/S improvement: target suppression minus desired loss.
- Effective receiver improvement: effective J/S improvement after noise-gain penalty.
- PVT/C/N0: GNSS-SDR health feedback used to decide whether a baseline is healthy.
- Transport health: overflows, timeouts, queue fill, FIFO state, clipping, and IQ peaks.
- Jammer activation: raw/calibrated/total-covariance power jump, generalized covariance gain, configured thresholds, current evidence, and the persistent detection latch.
- Preserve capture: live angle cluster center/spread/sample count, frozen measured U1, reference age, reference-to-cluster angle error, and frozen state.
- Weight transition: current/target complex weights, progress, total/completed chunks, duration, and reason.

The summary script warns that `R` is not jammer-only and `u1` is not always jammer. It reports measured-U1 validity and metrics without automatic method ranking. Negative total-output reduction means increased output power versus the reference; total output includes wanted signals, interference, noise, multipath, and receiver artifacts.

Dominant-vector suppression is not automatically jammer-only suppression. It is called jammer-like suppression only inside a jammer-like or explicitly marked jammer-on window.

The summary reads `logs/operator_events.log` when present. Without explicit markers, state labels remain healthy-like or jammer-like inferences and are not physical jammer ON/OFF truth.

The GUI has one measured-U1 target model curve. Schema-version-4
`full_angle_analysis` uses `common_measured_u1_target_response_scope` and
`common_model_music_comparison`; ideal-target fields are removed. Common and
shared target scan arrays remain in evidence for their respective consumers,
but are calculated once from the same accepted solve. Retired null-angle fields
and selected-null-grid metrics are removed; the plot marker identifies only the MUSIC guard candidate.
Neither target scan contains the per-PRN continuity scalar or intermediate
transition rows, and neither measures physical suppression.

## PVT position repeatability

CEP50/CEP95 now describe horizontal scatter about the mean of all received
fixes in the current receiver run. They do not use an authored latitude,
longitude or altitude, and do not measure error from a surveyed true position.
They are empirical nearest-rank50th/95th-percentile radii, not a Gaussian
conversion or a confidence bound on the unknown true location. A constant
position bias can therefore coexist with very small CEP.

Implementation: convert each WGS84 latitude/longitude to the ellipsoid surface
in ECEF; project differences onto the first fix's local east/north axes;
subtract the mean east/north coordinates of the complete run; take each
horizontal radius and select rank ceil(p*N). Altitude is excluded from this
horizontal statistic. ECEF avoids the longitude discontinuity at180degrees.
This local-plane metric is intended for stationary/local reception, not a
worldwide trajectory. Receiver latitude/longitude/altitude, DOPs and PVT
observations are still shown unchanged.

`gnss_accuracy_window_points` is the minimum publication count, now at least2
(profile2), not a rolling retention window. One fix shows warming rather than
misleading zero spread. All fixes remain stored for the receiver run; no new
long-duration memory/performance guarantee is claimed. Stale PVT hides CEP;
stopping clears the cumulative receiver state through the existing lifecycle.

The snapshot declares `cep_reference=run_mean` and
`cep_metric=horizontal_repeatability`; GUI readiness checks that contract and
its tooltip states the accuracy limitation. Headless runtime evidence and
receiver logs carry the same reference/CEP fields. The three configured static
truth coordinates, truth-availability metadata and absolute ENU/3D/window-error
outputs are removed, not retained as null aliases. Old configs with those keys
fail the existing unknown-field validation; external consumers must migrate.
Dated historical audits/results retain their original metric definitions.
