# Retained Verification and Measurement Records

Files in this directory are historical evidence tied to a particular run,
configuration, investigation, or benchmark. They are intentionally separate
from the living `docs/progress_tracker.md`.

An audit can accurately describe what was observed at its recorded time while
no longer describing current code. Amend a factual error explicitly; do not
silently rewrite an old result to match a later implementation.

| Record | Evidence represented |
| --- | --- |
| `live_test_2026-08-07.md` | Dated live anti-jamming test and observed RF/receiver behavior |
| `rf_power_sweep_tables.md` | RF power, compression, and J/S calculations and sweep tables |
| `someone_md_audit.md` | Layer-by-layer audit of the former `someone.md` claims |
| `live_test_2026-08-11.md` | Dated manual LCMV run |
| `self_run_live_evidence.md` | Procedure and evidence requirements for an independently run live test |
| `shared_u1_optimization_provenance.md` | Source-to-PVT changes, benchmark, and tests for Shared-U1 fanout |
| `failure_provenance_and_regression_2026-08-22.md` | Dated failure and regression evidence ledger |
| `cleanup_spark_hardware_2026-08-31.md` | Cleanup-branch software, X300/GNSS-SDR runtime, host-profile, and bounded bladeRF screen evidence |
| `bladerf_gain_main_control_offline_2026-09-01.md` | Confirmed splitter-path bladeRF gain sweep, cleanup versus untouched-`main` FIFO behavior, and direct-file SignalSim/GNSS-SDR validation |
| `sample_rate_sweep_2026-09-01.md` | Living 4–10 MS/s software and attached-hardware sweep, including the unresolved `main` FIFO-stall comparison |

These records do not collectively prove current hardware behavior. Consult the
living tracker for current code state and remaining verification boundaries.
