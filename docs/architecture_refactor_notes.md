# Architecture Refactor Notes

This repository is moving toward clearer ownership boundaries while preserving
runtime behavior.

## Current Source Of Truth

- Runtime values: `configs/antijamming/x300_realtime.json`
- Runtime schema/types: `src/antijamming/config/schemas/runtime.py`
- Log root: `logs/`

`StreamConfig` should describe fields and helper views. Runtime values should
come from the JSON profile, not Python defaults.

## Current Package Boundaries

- `src/antijamming/dsp/` - phase alignment, DoA, beamforming, and DSP pipeline
- `src/antijamming/radio/` - USRP device setup and host transport diagnostics
- `src/antijamming/gnss/` - GNSS-SDR process, FIFO handoff, parsing, and receiver state
- `src/antijamming/runtime/` - backend orchestration, worker threads, queue payloads, and UI metrics
- `src/antijamming/ui/` - PyQt window, widgets, theme, layout specs, and UI state models
- `src/antijamming/logging/` - logger names, file paths, and per-session reset

## Refactor Direction

High-value next steps:

1. Extract receiver projection logic from `ui/main_window.py` into
   `ui/state/receiver_projection.py`.
2. Split `runtime/backend.py` into RX loop, DSP stages, GNSS handoff, and UI
   metrics builder modules.
3. Split `gnss/gnss_sdr.py` into bridge, process control, config rendering,
   parsers, snapshot aggregation, and accuracy modules.
4. Split the single runtime config schema into subsystem schemas once call sites
   are cleaner.

Each step should be covered by focused tests and should avoid changing RF,
GNSS-SDR, calibration, or operator UI behavior unless explicitly requested.
