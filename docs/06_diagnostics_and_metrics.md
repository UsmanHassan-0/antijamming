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
