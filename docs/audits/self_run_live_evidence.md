# Independent Live Anti-Jamming Evidence Run

This procedure is for a run where the operator controls the physical bladeRF and jammer while the application records the USRP, DSP, LCMV, MUSIC, GNSS, carrier, code, and PVT evidence. Normal evidence capture is automatic and does not require the operator-marker buttons. Those optional buttons only add physical ground truth to distinguish a known bench switch action from a receiver-inferred RF change.

## What is retained for every Start-to-Stop session

`Start` creates `logs/runs/<UTC>_pid<PID>/` and writes the active path to `logs/CURRENT_SESSION`. `Stop` finalizes the manifest and writes that path to `logs/LATEST_SESSION`. The next Start recovers an unfinished prior run before truncating the stable root logs, so a process crash does not silently overwrite the previous evidence.

The run directory contains:

- `session_manifest.json`: UTC/local start and stop times, monotonic duration, outcome, and copied-artifact inventory.
- `operator_events.jsonl`: physical jammer/bladeRF markers, attenuation, bladeRF gain, LCMV state, current/target complex weights, transition progress, DoA, input/output digital powers, spatial state, and the nearest live GNSS state.
- `runtime_evidence.jsonl`: automatic synchronized snapshots at 0.1-second-or-better configured UI cadence. Every snapshot contains inferred RF state and its basis; LCMV enabled/mode/method; uniform, transition-start, current, target, and effective calibrated GNSS weights; ramp progress; phase alignment; MUSIC source diagnostics; DoA; channel/output powers; spatial detector state; suppression state; and GNSS/PVT state. State changes generate separate `automatic_runtime_state_transition` records.
- `tracking_observables.jsonl`: per-satellite receiver time/TOW, C/N0, Doppler, carrier phase in radians/cycles, code phase in samples/seconds, prompt I/Q/magnitude/phase, sample counter, correlation length, acquisition/symbol/word/pseudorange validity, PLL 180-degree lock flag, and cycle-slip flag.
- The normal `app`, UHD, stream, transport, phase, DoA, LCMV, spatial-vector, GNSS handoff, GNSS-SDR, health, UI, and error logs.
- Rendered GNSS-SDR configuration and receiver/console logs when they exist.

The tracking archive uses the configured GNSS-SDR `TrackingMonitor` on UDP 1236 with decimation 10. It is detailed receiver evidence, but it is not an RF sample-by-sample raw-IQ archive.

## Before Start

1. Start the bladeRF playback first. Confirm that the intended IQ file is still running and note its remaining duration.
2. Keep the physical jammer off.
3. Run `./run_realtime.sh`, but do not press `Start` yet.
4. Select `Configured jammer attenuation (dB)` and `Declared bladeRF SW gain (dB)`. The current defaults are 50 dB and 50 dB; select 55 dB when that is the bladeRF command used.
5. Press `Start`. When the USRP stream reports started, the GUI automatically logs the selected attenuation, selected bladeRF gain, and configured 45 dB USRP RX gain. No RF marker is required. `Record bladeRF ON` and `Record jammer OFF` are optional when a physically confirmed timestamp is desired. The settings are configured/declared values rather than measured RF power.

## Core preservation and jammer cycle

1. Leave LCMV off while the bladeRF-only baseline builds. Wait for current PVT and stable PRN bars. Use 120 seconds as the pre-jammer observation interval when the file duration permits.
2. Leave `MUSIC expected sources` at 1 for the bladeRF-only observation.
3. Enable `LCMV Test Nulling`. This action is logged automatically. In the current measured-preserve mode, enable first freezes the fresh bladeRF U1/covariance and remains on uniform weights until both jammer activation gates pass.
4. Confirm that PVT, stable PRNs, C/N0, carrier/code tracking, and prompt correlators remain healthy with LCMV armed and the jammer still off.
5. Physically turn the jammer on. The automatic evidence stream records the input-power jump, generalized-covariance change, activation decision, inferred state, DoA/MUSIC change, weight target and the complete smooth weight ramp. Optionally press `Record jammer ON` if a physically confirmed switch timestamp is needed.
6. Initially keep MUSIC sources at 1 long enough to record the one-source spectrum. Then change it to 2 to record the two-source spectrum. Each source-count change is logged.
7. Keep the jammer on for the intended observation interval. The application continuously records PVT snapshots and decimated carrier/code tracking; the GUI does not need to remain on one tab.
8. Physically turn the jammer off. Automatic evidence continues while the activation latch remains retained and separately reports whether current power-plus-covariance evidence still passes. This distinguishes “earlier jammer-like event remains latched” from “current jammer-like evidence.” Optionally press `Record jammer OFF` for physical ground truth. Keep LCMV enabled and observe whether the wanted signal remains/recovers without a weight discontinuity.
9. Disable LCMV and allow a fresh jammer-off bladeRF reference to build. Re-enable LCMV, verify preservation again, then repeat the physical jammer ON/marker/OFF/marker cycle. This is the required re-arm/recovery case.

## Additional robustness cases

Run these only while enough IQ file time remains:

- With LCMV already enabled, stop and restart bladeRF. Automatic power, GNSS and spatial-signature consequences are retained. Optional `Record bladeRF OFF/ON` markers identify the external process actions unambiguously. This does not claim that frozen weights can recreate a playback file after EOF.
- Move the active jammer, then record `tools/mark_rf_event.py --event jammer_moved --notes "physical position description"`. The GUI has ON/OFF buttons; the helper supplies the moved marker and free-form location note.
- When attenuation changes while the jammer is active, change the GUI attenuation field immediately. Editing completion records an `attenuation_db` event. The same applies to bladeRF software gain.

## Stop and produce reports

Press `Stop`; do not kill the process unless testing crash recovery. Then run:

```bash
.aj/bin/python tools/audit_live_session.py --logs logs
session_dir="$(<logs/LATEST_SESSION)"
.aj/bin/python tools/summarize_lcmv_run.py --logs "${session_dir}"
```

The first command writes `session_audit.json` and `session_audit.txt` into the run directory. Without any optional buttons, it reports automatic state transitions, inferred RF-state distributions, complete weight-state coverage, PVT loss during automatically inferred jammer-like periods, and inferred added-scene covariance suppression. If optional markers exist, it additionally reports physically labeled scenario coverage, before/after carrier continuity and explicit jammer-window PVT continuity.

The second command reports all existing spatial and output-power metrics, including LCMV intervals and the jammer-only suppression estimate.

## What the suppression numbers mean in this implementation

- `measured_output_reduction_vs_uniform_db` compares the actual one-stream beamformed IQ power against the power that the uniform combiner would have produced from the same four-channel covariance. It is total output reduction and can include wanted-signal loss.
- `measured_output_reduction_vs_raw_avg_channel_db` and `...raw_sum_channels_db` use different input-power reference conventions. They are useful accounting metrics, not jammer-only suppression.
- The runtime automatically forms a positive-semidefinite projection of `R_current - R_arm`, evaluates uniform and actually applied weights against the same excess covariance, aggregates linear powers, and reports `inferred_added_scene_suppression_db` during automatically inferred jammer-like periods. This needs no button, but it is added-scene suppression rather than proven jammer-only suppression.
- An optional physical `jammer_on` marker allows the same estimator to be reported as physically labeled `jammer_only_suppression_db`, provided the rest of the scene stayed unchanged.
- A model null near -300 dB is floating-point evaluation of an ideal mathematical constraint. It is not a measured RF or digital-IQ suppression claim.

## Evidence interpretation limits

- `pvt_loss_observed=false` means no loss occurred at the logged one-second GNSS snapshot cadence in that marked window. It is strong run evidence, not a claim about unobserved intervals shorter than the logging cadence.
- A carrier-phase residual is measured relative to a local linear phase prediction across the marker. It exposes abrupt discontinuity, but multipath, Doppler dynamics, receiver-loop response, and marker timing also affect it. Inspect it together with cycle-slip, PLL lock, prompt magnitude, C/N0, stable satellites, and PVT.
- Missing operator markers do not prevent automatic logging, inference, LCMV analysis, weight-ramp proof, PVT continuity analysis or added-scene suppression. They prevent only a claim that an inferred RF change is physically proven jammer-only.
- If the bladeRF file ends, mark `bladeRF_off`. There is then no wanted waveform for a frozen spatial constraint to preserve.
