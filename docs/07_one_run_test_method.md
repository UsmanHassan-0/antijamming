# One Run Test Method

Separate jammer-off and jammer-on lab runs are useful validation, but the runtime algorithm should not require permanent bladeRF-only or jammer-only training vectors.

The one-run segmentation labels are evidence labels, not physical jammer ON/OFF truth unless an operator marker/log explicitly provides that truth:

- `startup`: not enough healthy reference evidence yet.
- `healthy_baseline`: PVT current/fixed, enough observations, C/N0 above threshold, and LCMV off under uniform combining.
- `lcmv_on_no_jammer`: LCMV is active while jammer confidence remains low.
- `jammer_like_event`: current dominant vector differs from the healthy reference and GNSS health degrades or jammer-like covariance evidence appears.
- `recovery`: PVT/C/N0 recover and the current vector moves back toward the healthy reference.
- `unknown`: evidence is insufficient or mixed.

Healthy-reference tracking stores a smoothed vector, covariance, internal/display angle, timestamp, and confidence. It runs from the DoA loop while LCMV is off and the FIFO uses the uniform sum. It freezes whenever LCMV is enabled, including its temporary uniform fallback, and also freezes for unhealthy PVT/observations/C/N0, a large angle or power jump, high jammer confidence, or suspicious eigen-gap/effective-rank/peak structure. Logs include `healthy_reference_available`, age, angle, coherence with current `u1`, update reason, freeze reason, `healthy_reference_updated`, `healthy_reference_freeze_reasons`, `lcmv_safe_baseline`, and confidence scores.

Record physical bench changes with `tools/mark_rf_event.py`, for example:

```bash
tools/mark_rf_event.py --event jammer_on --attenuation-db 50
tools/mark_rf_event.py --event jammer_off
tools/mark_rf_event.py --event bladeRF_off
```

The helper appends JSONL to `logs/operator_events.log`. Without these markers, the summary labels a `jammer_like_event` only as an RF/GNSS inference and does not claim physical jammer truth.

Warnings to inspect:

- `warning_lcmv_on_while_jammer_confidence_low`
- `warning_active_null_may_target_healthy_reference`
