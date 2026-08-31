# Hardware-Free Code Hygiene Audit — 2026-08-31

## Scope and branch isolation

- Baseline: `main` at `0672377aa6c3fa53a11e09747e0cbd300c815579`.
- Cleanup branch: `cleanup/no-usrp-verification-20260831`.
- No USRP was attached, so this audit does not claim RF, UHD hardware, or live GNSS-SDR validation.
- `main` and `per-prn-fifo-experimental` were not modified. The experimental branch remains outside this cleanup scope.
- Large-file architecture work, including splitting `runtime/backend.py`, is deliberately deferred.

## Proven findings and changes

1. `UsrpRxDevice._wait_for_lo_lock()` had an unreachable sleep after its polling loop. An unlocked TwinRX could therefore busy-spin against UHD for the full timeout. The sleep now executes between polls, with fake-clock tests for lock transition and timeout behavior.
2. `JsonIpcServer.close()` could race a just-accepted connection: it snapshotted sessions before the accept thread necessarily registered its last connection. Shutdown now closes and joins the acceptor before closing and joining the complete session set. An eight-client regression test and repeated lifecycle runs cover this path.
3. Coverage instrumentation exposed a native Qt abort in deferred PyQtGraph scene destruction. GDB placed the abort in `QGraphicsScene::itemsBoundingRect()` through `QGraphicsItem::__cxa_pure_virtual`. GUI timers are now widget-owned and stopped on close; the Qt test fixture completes deferred deletion before Python garbage collection.
4. `gnss_accuracy_log_interval_s` existed only in the schema and JSON profile and had no reader. It was removed.
5. External bladeRF/jammer settings and bench geometry were removed from the default realtime product profile. They remain preserved as the explicit optional overlay `configs/experiments/realtime_measured_bladerf_preserve_lcmv_test.json`.
6. Static cleanup removed unused imports, assignments, and one misleading dynamic `setattr`; the generated protobuf modules are explicitly excluded only from checks that conflict with protoc output.

The optional preserved bench manifest can be selected with:

```bash
ANTIJAM_RUNTIME_OVERLAY=configs/experiments/realtime_measured_bladerf_preserve_lcmv_test.json ./run_realtime.sh
```

## Verification evidence

- Normal suite: `301 passed, 1 deselected` (`usrp` hardware test deselected).
- Coverage-instrumented suite: five consecutive full passes after the Qt teardown fix; measured source coverage was 73%.
- Focused configuration/GNSS/USRP tests: `129 passed`.
- Ruff configured hardware-free correctness gate: clean.
- Vulture at 90% confidence: no remaining findings.
- Python `compileall`: clean.
- IPC/USRP/GNSS lifecycle subset: repeated runs passed.
- `git diff --check`: clean.

## Remaining limits

These checks prove deterministic Python behavior with fakes, sockets, FIFOs, subprocess models, and offscreen Qt. They do not prove TwinRX LO locking on the physical device, UHD receive/stop timing on the X300, actual GNSS-SDR acquisition/PVT, RF link-budget accuracy, antenna geometry, or over-the-air anti-jamming effectiveness. Those require the attached bench and must be recorded as a separate hardware run.
