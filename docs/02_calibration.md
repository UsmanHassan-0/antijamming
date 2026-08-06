# Calibration

Runtime calibration is loaded in `src/antijamming/app/main.py` with `load_calibration_correction_selection()` from `src/antijamming/dsp/phase/alignment.py`. The selected vector becomes `cfg.phase_correction_vector` and is applied by `apply_phase_calibration()`.

Phase-only calibration rotates each channel so coherent phase lines up. In kid terms: it makes all four channels clap at the same time. Complex-gain calibration also scales magnitudes. It makes them clap at comparable loudness too. The product default is `calibration_correction_mode: "complex_gain"`.

The calibration files under `configs/calibration/` are conducted splitter calibrations. They correct receiver-chain gain/phase differences measured through a splitter, but they do not perfectly model the OTA antenna manifold, antenna coupling, multipath, or physical jammer/GNSS arrival geometry.

To verify runtime correction, inspect:

- `calibration_manifest` in `app.log` or `analysis.log`
- `calibration_correction_mode_applied`
- `applied_correction_magnitudes`
- `applied_correction_phases_deg`
- `cal_power_spread_db` versus `raw_power_spread_db` in `phase_alignment.log` or `stream_health.log`

If complex-gain mode is active and calibrated power spread is identical to raw power spread, verify that the complex-gain vector exists and was selected, not silently falling back to phase-only.
