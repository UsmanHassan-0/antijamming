# One Run Test Method

Separate jammer-off and jammer-on lab runs are useful validation, but the runtime algorithm should not require permanent bladeRF-only or jammer-only training vectors.

The one-run segmentation labels are evidence labels, not physical jammer ON/OFF truth unless an operator marker/log explicitly provides that truth:

- `startup`: not enough healthy reference evidence yet.
- `healthy_baseline`: PVT current/fixed, enough observations, C/N0 above threshold, and not yet armed under uniform combining.
- `lcmv_on_no_jammer`: evidence label for an armed state with healthy GNSS and low vector/receiver-based jammer confidence. This is not the separate power/covariance activation gate, nor proof of physical jammer absence; inspect current protection and applied weights separately.
- `jammer_like_event`: current dominant vector differs from the healthy reference and GNSS health degrades or jammer-like covariance evidence appears.
- `recovery`: PVT/C/N0 recover and the current vector moves back toward the healthy reference.
- `unknown`: evidence is insufficient or mixed.

Healthy-reference tracking stores a smoothed vector, covariance, internal/display angle, timestamp, and confidence. It runs from the DoA loop before automatic arming, while every FIFO uses the shared uniform spatial row. It freezes after arming, including uniform fallback/recovery, and also freezes for unhealthy PVT/observations/C/N0, a large angle or power jump, high jammer confidence, or suspicious eigen-gap/effective-rank/peak structure. Logs include `healthy_reference_available`, age, angle, coherence with current `u1`, update reason, freeze reason, `healthy_reference_updated`, `healthy_reference_freeze_reasons`, `lcmv_safe_baseline`, and confidence scores.

The product arms automatically only after the live angle cluster and measured
healthy U1 are fresh, consistent and backed by healthy GNSS. Arming freezes
that vector/covariance and leaves the FIFO uniform. There is no manual LCMV
toggle. A null needs both input-power and generalized-covariance activation
thresholds. Current protection releases after valid lower evidence persists
for the hold interval, while `lcmv_jammer_detected_latched` records historical
detection until the next run. Further jammer evidence can reactivate protection
without re-arming. The runtime logs inferred added-scene covariance suppression;
optional physical markers supply additional labeling, not algorithm controls.

Continuous receiver evidence does not require marker buttons. `runtime_evidence.jsonl` automatically stores inferred RF state, LCMV state, all combiner weights and ramp progress, powers, spatial state and GNSS state. The GUI `Record jammer ON/OFF` and `Record bladeRF ON/OFF` buttons are optional physical-ground-truth annotations. They record physical state and an optional note, not transmitter settings or RF power. `tools/mark_rf_event.py` remains available for notes and `jammer_moved`, for example:

```bash
tools/mark_rf_event.py --event jammer_on --notes "physical switch enabled"
tools/mark_rf_event.py --event jammer_off
tools/mark_rf_event.py --event bladeRF_off
```

The helper and GUI append atomic JSONL to both `logs/operator_events.log` and the current run's `operator_events.jsonl`. Without them, the audit still reports automatic inference, transitions, weights, tracking continuity and added-scene suppression, but does not claim physical jammer truth. See `docs/audits/self_run_live_evidence.md` for the complete preservation, jammer-cycle, re-arm, carrier-continuity, and report procedure.

Warnings to inspect:

- `warning_lcmv_on_while_jammer_confidence_low`
- `warning_active_null_may_target_healthy_reference`
