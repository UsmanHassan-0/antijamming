# System Architecture

This repo runs a USRP X300/TwinRX four-channel GNSS anti-jam pipeline. The active runtime path is `src/antijamming/app/main.py` -> `src/antijamming/runtime/worker.py` -> `src/antijamming/runtime/backend.py`.

The hardware stream is four coherent IQ channels from the X300. The configured order in `configs/antijamming/x300_realtime.json` is preserved and must not be changed for algorithm experiments. Runtime calibration is applied before MUSIC/Bartlett, spatial-vector diagnostics, LCMV candidate evaluation, and the GNSS-SDR FIFO combiner. The calibration loader in `src/antijamming/dsp/phase/alignment.py` supports both `phase_only` and `complex_gain`; the product default is `calibration_correction_mode: "complex_gain"`.

The DSP path estimates DoA with MUSIC and keeps Bartlett diagnostics. Candidate beamformers are computed in `src/antijamming/dsp/beamforming/lcmv.py` and selected by `lcmv_test_null_method`. The current product config selects `covariance_lcmv_ideal`. Only one active beamformer feeds GNSS-SDR at a time through the existing FIFO path; other candidate methods are diagnostic unless selected.

GNSS-SDR is not modified by this anti-jam framework. The backend writes one complex64 IQ stream into the existing FIFO and reads GNSS-SDR feedback through bridge snapshots: PVT status, observations, C/N0, receiver age, and UDP monitor state. Those health fields feed one-run segmentation and healthy-reference tracking.

Logs live under `logs/`. The key files for this work are `analysis.log`, `lcmv.log`, `lcmv_pattern_absolute.jsonl`, `spatial_vector_diagnostics.jsonl`, `phase_alignment.log`, `gnss_handoff.log`, and `stream_health.log`. `lcmv_heavy_diagnostics_interval_s` throttles the full LCMV JSON diagnostics. `tools/summarize_lcmv_run.py` reads the logs and prints calibration state, RF budget, active LCMV method, candidate rankings, and run-state summaries.
