# Source Audit

Last updated: 2026-06-06

This audit tracks the current purpose of each source file and where code can be
made smaller or faster without changing the 4 MHz RF path. Runtime values should
come from `configs/antijamming/x300_realtime.json`; Python schema/config code
should describe and validate the profile, not hide operational defaults.

## Current Cleanup

- Removed generated `__pycache__/` and `*.pyc` files from `src/`.
- Removed the stale secondary Matplotlib skyplot path. The repo now keeps one
  skyplot implementation: `ui/widgets/skyplot/monitor.py`.
- Removed disabled DoA comparison profile/schema/runtime code.
- Renamed `ui/widgets/plots/engineering.py` to
  `ui/widgets/plots/algorithm.py`; these are reusable realtime plot builders,
  not an engineering tab.
- Simplified `run_realtime.sh` so it no longer performs fixed-USRP-IP probing.
  Host link and image setup remain owned by `setup.sh`; runtime XG image
  validation remains in USRP device setup.

## Runtime Performance Focus

The system should keep USRP sampling and bandwidth at the configured RF profile
rate. Optimization should happen in queues, handoff, DSP scheduling, and UI
refresh.

Runtime timing now records:

- RX recv time
- RX GNSS queue publish time
- GNSS queue wait time
- GNSS beamform compute time
- GNSS FIFO write time
- DSP phase time
- DSP DoA time
- DSP LCMV time
- UI refresh breakdown

`gnss_queue_wait` includes normal idle waits when the queue is empty. Bottlenecks
are more likely when `gnss_beamform_compute`, `gnss_fifo_write`, DSP stage times,
or raw queue replacement counts are high.

## File Inventory

| File | Status | Notes |
| --- | --- | --- |
| `app/main.py` | Keep | CLI entrypoint; enough code for argument parsing and launch flow. |
| `config/loader.py` | Keep | Converts JSON profile into runtime schema. Keep profile values in JSON. |
| `config/paths.py` | Keep | Central path helper. |
| `config/schemas/runtime.py` | Keep, future split | Single schema is acceptable short term, but should be split by subsystem after call sites are cleaner. |
| `detection/jammer/detector.py` | Keep | Small detector module. |
| `dsp/beamforming/lcmv.py` | Keep | Core beamforming math. Do not optimize by hiding array math unless benchmarked. |
| `dsp/doa/music.py` | Keep | Core MUSIC implementation. |
| `dsp/models.py` | Keep | Shared DSP payload models. |
| `dsp/phase/alignment.py` | Keep | Phase calibration/alignment logic. |
| `dsp/pipeline/stages.py` | Keep, split later | Real DSP stage orchestration. Good future split: phase metrics, DoA metrics, LCMV metrics. |
| `gnss/constellations.py` | Keep | Single place for GPS, BeiDou, Galileo, and GLONASS filtering/display labels. |
| `gnss/gnss_sdr.py` | Keep, high-priority split | Largest file. Split into process control, FIFO bridge, config rendering, line parsing, snapshot state, and accuracy/truth helpers. |
| `logging/setup.py` | Keep | Owns log names and root log layout. |
| `radio/transport/host.py` | Keep | Setup/diagnostic support for host transport. |
| `radio/usrp/device.py` | Keep, trim later | Owns runtime USRP setup and XG image enforcement. Keep runtime validation here. |
| `radio/usrp/discovery.py` | Keep | Small discovery helper. Runtime launcher no longer needs fixed-IP probing. |
| `runtime/backend.py` | Keep, high-priority split | Hot orchestration path. Split only along real threads/loops: RX, DSP, GNSS handoff, health/timing. |
| `runtime/latest_queue.py` | Keep | Small latest-value queue abstraction. |
| `runtime/ui_metrics.py` | Keep | UI payload model. Consider grouping fields by phase/DoA/LCMV/GNSS if callers get cleaner. |
| `runtime/work_items.py` | Keep | Queue item models. |
| `runtime/worker.py` | Keep | Worker-thread helper. |
| `ui/accessibility.py` | Keep | Accessibility/palette helpers. |
| `ui/main_window.py` | Keep, high-priority split | Largest UI file. Split receiver projection, card construction, algorithm plot updates, and health label updates. |
| `ui/specs.py` | Keep | UI constants; helps avoid repeated magic numbers. |
| `ui/state/receiver.py` | Keep, split later | Receiver state projection. Good candidate for moving derived display logic out of `main_window.py`. |
| `ui/theme/tokens.py` | Keep | Theme tokens. |
| `ui/widgets/cards/components.py` | Keep | Reusable card/chip builders. |
| `ui/widgets/plots/algorithm.py` | Keep | Shared pyqtgraph styling and DoA/LCMV/PRN plot builders. |
| `ui/widgets/prn_monitor/monitor.py` | Keep | PRN chart. Display rate can stay lower than DSP rate. |
| `ui/widgets/skyplot/monitor.py` | Keep | Single skyplot implementation. It should remain size-driven by its card/window. |

Package `__init__.py` files are kept only for package exports. They should stay
short and avoid runtime side effects.

## Next Refactors

1. Split `gnss/gnss_sdr.py` first. It is the biggest file and mixes process
   control, FIFO writing, config generation, parsing, and receiver state.
2. Split `runtime/backend.py` around actual runtime loops after timing data from
   a fresh run shows which loop is slow.
3. Split `ui/main_window.py` by UI responsibility, not by arbitrary card count:
   receiver projection, plot updates, status labels, and layout construction.
4. Keep one skyplot and one PRN monitor. Do not re-add duplicate graph libraries
   unless a benchmark or visual requirement justifies the extra dependency.
5. Keep GNSS-SDR input rate matched to the USRP profile unless logs prove a FIFO
   or process-rate mismatch. Current available config showed 4 Msps matching the
   RF profile.
