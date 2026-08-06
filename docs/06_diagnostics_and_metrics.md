# Diagnostics And Metrics

The backend writes candidate diagnostics into `spatial_vector_diagnostics.jsonl` and `analysis.log`. Full LCMV JSON diagnostics are throttled by `lcmv_heavy_diagnostics_interval_s`; concise LCMV status can still update more often. `tools/summarize_lcmv_run.py` summarizes them.

Only `covariance_lcmv_ideal`, `measured_dominant_eigenvector`, and `covariance_lcmv_measured_u1` are live candidates. The first is the configured product method; the two measured-u1 methods are diagnostic/optional unless explicitly selected.

Important metrics:

- Ideal steering suppression: predicted reduction of the ideal steering component.
- U1 measured component suppression: predicted reduction of the dominant measured covariance eigenvector.
- Total output reduction: measured or covariance-predicted drop in total output power. This is not jammer-only suppression.
- Jammer-only suppression: unavailable unless the run has explicit jammer-off/LCMV-off, jammer-on/LCMV-off, and jammer-on/LCMV-on windows with positive baseline-subtracted powers. The summary uses the stored healthy covariance to adjust the LCMV-on baseline; the runtime logs `jammer_only_suppression_estimate_available=false` when a single chunk cannot prove those windows.
- Desired loss: response loss versus the healthy reference vector, when available.
- Noise gain: weight power, usually `10log10(||w||^2)` or relative to a reference.
- Effective J/S improvement: target suppression minus desired loss.
- Effective receiver improvement: effective J/S improvement after noise-gain penalty.
- PVT/C/N0: GNSS-SDR health feedback used to decide whether a baseline is healthy.
- Transport health: overflows, timeouts, queue fill, FIFO state, clipping, and IQ peaks.

The summary script explicitly warns that `R` is not jammer-only and `u1` is not always jammer. It ranks candidates by dominant/u1 suppression, total output reduction, and effective receiver improvement. Negative total-output reduction means the candidate increased total measured/covariance output power versus the reference; it does not automatically mean jammer suppression failed, because total output includes desired signal, sky GNSS, noise, multipath, and receiver artifacts.

Dominant-vector suppression is not automatically jammer-only suppression. It is called jammer-like suppression only inside a jammer-like or explicitly marked jammer-on window.

The summary reads `logs/operator_events.log` when present. Without explicit markers, state labels remain healthy-like or jammer-like inferences and are not physical jammer ON/OFF truth.
