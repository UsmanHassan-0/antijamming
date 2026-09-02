# Progress Tracker

| Phase | Goal | Implemented | Verified in code | Verified in logs | RF test passed | Known issues | Next action |
|---|---|---:|---:|---:|---:|---|---|
| Phase 0 | USRP stream to GNSS-SDR | yes | yes | partial | partial | Requires live X300 for full proof | Keep transport/FIFO tests green |
| Phase 1 | baseline PVT | yes | yes | partial | partial | Depends on live sky and GNSS-SDR state | Record clean baseline log set |
| Phase 2 | phase calibration | yes | yes | yes | partial | Conducted calibration only | Recheck after RF chain changes |
| Phase 3 | complex-gain calibration | yes | yes | yes | partial | Not OTA manifold calibration | Compare raw/cal power spreads in run logs |
| Phase 4 | steering model and angle convention | yes | yes | yes | no | Physical orientation still needs RF proof | Keep display/internal tests |
| Phase 5 | MUSIC/Bartlett validation | yes | yes | partial | partial | MUSIC and Bartlett can disagree | Capture controlled angle sweep |
| Phase 6 | product `covariance_lcmv_ideal` | yes | yes | yes | partial | Ideal null vector may not match OTA manifold | Compare ideal-vector and measured-u1 diagnostics |
| Phase 7 | spatial-vector diagnostics | yes | yes | yes | partial | u1 is not always jammer | Use healthy-reference warnings |
| Phase 8 | desired-loss/noise-gain diagnostics | yes | yes | yes | no | Healthy reference may be unavailable early | Validate during one-run test |
| Phase 9 | measured-u1 LCMV active test | yes | yes | partial | no | Can null SOI if u1 is healthy | Enable only when jammer confidence is high |
| Phase 10 | full covariance LCMV/MVDR | yes | yes | partial | no | SOI inside R can be damaged | Test active `covariance_lcmv_ideal` with operator markers |
| Phase 11 | GSC exploration | no | no | no | no | Future work | Design blocking matrix and gate logic |
| Phase 12 | automated jammer confidence/gating | partial | yes | partial | no | Thresholds need RF tuning | Tune against labeled runs |

## 2026-09-04 — jammer protection release on `main`

- Implemented separate live and historical state: `lcmv_jammer_protection_active`
  controls the beam/FIFO path, while `lcmv_jammer_detected_latched` records that
  an activation occurred during the current armed run.
- Protection activates only when the existing input-power and generalized-
  covariance upper gates both pass. It releases only after both valid metrics
  remain below lower hysteresis thresholds for the configured hold time.
- Missing, malformed, or hysteresis-band evidence cannot release protection;
  new upper-gate evidence reactivates it.
- Release is evaluated even when MUSIC no longer supplies a valid target. On
  release, common output and shared-U1 FIFO fanout return to uniform behavior.
- Operator disable is serialized against an in-flight LCMV update so a DSP
  update that started earlier cannot restore active weights after disable.
- Verification on the laptop worktree: backend/config/FIFO focus set 94 passed;
  GUI status file 48 passed; complete suite 303 passed and 1 skipped. These are
  deterministic software tests, not jammer-on or RF-hardware proof.
