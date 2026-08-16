# Next Steps And Open Questions

Next implementation steps:

- Stop the orphan duplicate UHD/backend owner before the next authoritative run.
- Restart the active GUI once to load the measured-U1 preserve, activation latch, and smooth-weight code now on disk.
- Capture one labeled live run using `tools/mark_rf_event.py` for jammer, bladeRF, attenuation, and LCMV changes.
- Use `tools/summarize_lcmv_run.py` to compare candidate rankings against PVT/C/N0 behavior.
- Tune `lcmv_max_white_noise_gain_db` and
  `lcmv_min_predicted_jammer_suppression_db` from real run logs. Desired loss is
  logged as a diagnostic, not used as a selectable guard or fallback threshold.
- Tune the 3 dB input-power and 6 dB generalized-covariance activation thresholds from labeled low-power jammer steps without permitting angle-only false activation.
- Add a future GSC design after full covariance LCMV is validated.

Open questions:

- How stable is the OTA manifold compared with the conducted splitter calibration?
- Which candidate method improves PVT/C/N0 under the real jammer without harming jammer-off baseline?
- What GNSS health thresholds best mark healthy baseline versus jammer-like event?
- Does the frozen measured bladeRF U1 preserve every PRN's C/N0 and PVT through a sustained jammer interval, or is a multi-vector desired subspace required?
