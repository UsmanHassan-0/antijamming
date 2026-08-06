# Next Steps And Open Questions

Next implementation steps:

- Capture one labeled live run using `tools/mark_rf_event.py` for jammer, bladeRF, attenuation, and LCMV changes.
- Use `tools/summarize_lcmv_run.py` to compare candidate rankings against PVT/C/N0 behavior.
- Tune `lcmv_max_white_noise_gain_db`, `lcmv_max_desired_loss_db`, and `lcmv_min_predicted_jammer_suppression_db` from real run logs.
- Decide whether measured-U1 methods should require a higher jammer-confidence gate before activation.
- Add a future GSC design after full covariance LCMV is validated.

Open questions:

- How stable is the OTA manifold compared with the conducted splitter calibration?
- Which candidate method improves PVT/C/N0 under the real jammer without harming jammer-off baseline?
- What GNSS health thresholds best mark healthy baseline versus jammer-like event?
- Is an explicit desired steering/pass constraint needed beyond uniform-sum preservation and healthy-reference diagnostics?
