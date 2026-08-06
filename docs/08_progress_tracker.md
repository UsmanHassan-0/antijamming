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
