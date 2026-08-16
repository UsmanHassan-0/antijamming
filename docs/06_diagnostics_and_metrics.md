# Diagnostics And Metrics

The backend writes candidate diagnostics once into `spatial_vector_diagnostics.jsonl`. Full LCMV response JSON is written once into `lcmv_pattern_absolute.jsonl`; both are throttled by `lcmv_heavy_diagnostics_interval_s`, while concise LCMV status can still update more often. `analysis.log` retains the full-angle MUSIC/Bartlett snapshots without duplicating those dedicated records. `tools/summarize_lcmv_run.py` reads both current dedicated files and legacy duplicated runs.

Only `covariance_lcmv_ideal` and `covariance_lcmv_measured_u1` are live candidates. Both call the same full-covariance LCMV solver. The first is the configured ordinary product method. The measured-u1 method is diagnostic/optional in the ordinary one-stream path, but its accepted row is deliberately used by the optional shared measured-U1 phase-continuous GNSS fanout.

Important metrics:

- Ideal steering suppression: predicted reduction of the ideal steering component.
- U1 measured component suppression: predicted reduction of the dominant measured covariance eigenvector.
- Total output reduction: measured or covariance-predicted drop in total output power. This is not jammer-only suppression.
- Added-scene suppression: the runtime PSD-projects `R_current - R_arm`, then applies the uniform and actually applied weights to that same covariance. This avoids mixing desired-signal loss into the numerator merely because the weights changed. The automatic activation latch is only a safety gate, not proof that the added scene is the physical jammer.
- Jammer-only suppression: the summary accepts the runtime same-covariance result as jammer-only only inside an explicit operator `jammer_on` marker window with the bladeRF scene otherwise unchanged. If those runtime fields are unavailable, the legacy fallback requires explicit jammer-off/LCMV-off, jammer-on/LCMV-off, and jammer-on/LCMV-on windows with positive baseline-subtracted powers.
- Desired loss: response loss versus the healthy reference vector, when available.
- Noise gain: weight power, usually `10log10(||w||^2)` or relative to a reference.
- Effective J/S improvement: target suppression minus desired loss.
- Effective receiver improvement: effective J/S improvement after noise-gain penalty.
- PVT/C/N0: GNSS-SDR health feedback used to decide whether a baseline is healthy.
- Transport health: overflows, timeouts, queue fill, FIFO state, clipping, and IQ peaks.
- Jammer activation: raw/calibrated/total-covariance power jump, generalized covariance gain, configured thresholds, current evidence, and the persistent detection latch.
- Preserve capture: live angle cluster center/spread/sample count, frozen measured U1, reference age, reference-to-cluster angle error, and frozen state.
- Weight transition: current/target complex weights, progress, total/completed chunks, duration, and reason.

The summary script explicitly warns that `R` is not jammer-only and `u1` is not always jammer. It ranks candidates by dominant/u1 suppression, total output reduction, and effective receiver improvement. Negative total-output reduction means the candidate increased total measured/covariance output power versus the reference; it does not automatically mean jammer suppression failed, because total output includes desired signal, sky GNSS, noise, multipath, and receiver artifacts.

Dominant-vector suppression is not automatically jammer-only suppression. It is called jammer-like suppression only inside a jammer-like or explicitly marked jammer-on window.

The summary reads `logs/operator_events.log` when present. Without explicit markers, state labels remain healthy-like or jammer-like inferences and are not physical jammer ON/OFF truth.
