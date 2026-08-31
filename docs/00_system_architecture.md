# System Architecture

This repo runs a USRP X300/TwinRX four-channel GNSS anti-jam pipeline. The
active GUI path is `src/antijamming/app/main.py` -> a separate
`src/antijamming/app/headless.py` service process -> local JSON-line IPC ->
`src/antijamming/runtime/backend.py`. The GUI-side
`runtime/remote_worker.py` carries commands and telemetry; it does not own the
USRP.

The hardware stream is four coherent IQ channels from the X300. The configured order in `configs/antijamming/x300_realtime.json` is preserved and must not be changed for algorithm experiments. Runtime calibration is applied before MUSIC/Bartlett, spatial-vector diagnostics, LCMV candidate evaluation, and the GNSS-SDR FIFO combiner. The calibration loader in `src/antijamming/dsp/phase/alignment.py` supports both `phase_only` and `complex_gain`; the product default is `calibration_correction_mode: "complex_gain"`.

The DSP path estimates DoA with MUSIC and keeps Bartlett diagnostics. The only nulling method in `src/antijamming/dsp/beamforming/lcmv.py` is `covariance_lcmv_measured_u1`. One accepted measured-vector solve supplies the common target and Shared-U1 protection target. A PRN-specific complex continuity scalar and the existing transitions precede dynamically assigned GNSS-SDR FIFOs; this is not an independent LCMV solve per PRN. The ideal-null solver and its public export are absent.

MUSIC supplies a covariance-spectrum estimate. Its strongest eligible peak
outside the frozen bladeRF guard remains a control prerequisite, but does not
supply the null constraint. Weights activate only after power and covariance
evidence. The GUI has one calculated steering-model scan of the measured-U1
target; its marker is labelled MUSIC guard candidate, not measured null bearing.

The repository contains a customized, vendored GNSS-SDR tree under
`gnss-sdr/`; it is not an untouched external system package. The Python bridge
implementation and public exports live directly under
`src/antijamming/gnss/sdr_bridge/`. In the current dynamic Shared-U1 profile,
the bridge renders and writes multiple complex64 FIFO source streams (ten
configured `1C` channels), then reads GNSS-SDR feedback through
PVT/NMEA/tracking monitor snapshots. Those health fields feed one-run
segmentation and healthy-reference tracking.

GNSS bridge maps use one internal satellite key shape:
`(normalized_constellation, PRN)`. Every public per-satellite record carries
`constellation`, numeric `prn`, and a constellation-qualified `satellite_id`;
summary lists contain only labels such as `G05`, `C05`, or `R03`. GPS-only
integer summary aliases are not part of the current IPC/UI contract.

Logs live under `logs/`. The key files are `analysis.log`, `lcmv.log`, `lcmv_pattern_absolute.jsonl`, `spatial_vector_diagnostics.jsonl`, `phase_alignment.log`, `gnss_handoff.log`, and `stream_health.log`. `lcmv_heavy_diagnostics_interval_s` throttles full diagnostics. `tools/summarize_lcmv_run.py` reports calibration, measured-U1 metrics, and run state without ranking retired methods. RF-link-budget calculation is outside the product runtime and its analysis tools.
