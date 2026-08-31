# System Architecture

This repo runs a USRP X300/TwinRX four-channel GNSS anti-jam pipeline. The
active GUI path is `src/antijamming/app/main.py` -> a separate
`src/antijamming/app/headless.py` service process -> local JSON-line IPC ->
`src/antijamming/runtime/backend.py`. The GUI-side
`runtime/remote_worker.py` carries commands and telemetry; it does not own the
USRP.

The hardware stream is four coherent IQ channels from the X300. The configured order in `configs/antijamming/x300_realtime.json` is preserved and must not be changed for algorithm experiments. Runtime calibration is applied before MUSIC/Bartlett, spatial-vector diagnostics, LCMV candidate evaluation, and the GNSS-SDR FIFO combiner. The calibration loader in `src/antijamming/dsp/phase/alignment.py` supports both `phase_only` and `complex_gain`; the product default is `calibration_correction_mode: "complex_gain"`.

The DSP path estimates DoA with MUSIC and keeps Bartlett diagnostics. Candidate beamformers are computed in `src/antijamming/dsp/beamforming/lcmv.py` and selected by `lcmv_test_null_method`. The current product config selects `covariance_lcmv_ideal`. Only one active spatial solution feeds GNSS-SDR at a time; other candidate methods are diagnostic unless selected. With the product's Shared-U1 mode enabled, that shared spatial solution is multiplied by a PRN-specific complex continuity scalar and fanned out to dynamically assigned GNSS-SDR channel FIFOs. This is not an independent LCMV solve per PRN.

The repository contains a customized, vendored GNSS-SDR tree under
`gnss-sdr/`; it is not an untouched external system package. The Python bridge
implementation is split under `src/antijamming/gnss/sdr_bridge/`, with
`gnss/gnss_sdr.py` retained as a compatibility facade. In the current dynamic
Shared-U1 profile, the bridge renders and writes multiple complex64 FIFO source
streams (ten configured `1C` channels), then reads GNSS-SDR feedback through
PVT/NMEA/tracking monitor snapshots. Those health fields feed one-run
segmentation and healthy-reference tracking.

Logs live under `logs/`. The key files for this work are `analysis.log`, `lcmv.log`, `lcmv_pattern_absolute.jsonl`, `spatial_vector_diagnostics.jsonl`, `phase_alignment.log`, `gnss_handoff.log`, and `stream_health.log`. `lcmv_heavy_diagnostics_interval_s` throttles the full LCMV JSON diagnostics. `tools/summarize_lcmv_run.py` reads the logs and prints calibration state, RF budget, active LCMV method, candidate rankings, and run-state summaries.
