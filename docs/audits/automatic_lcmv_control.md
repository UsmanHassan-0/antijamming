# Automatic-only LCMV control

Implementation/evidence record, opened 2026-09-14T11:19:12Z. Current progress
belongs in [the single tracker](../progress_tracker.md). This record describes
local software changes, not a new RF or receiver measurement.

## Baseline and authorization

- User requests removal of manual LCMV control because the product is automatic.
- Laptop branch: `cleanup/no-usrp-verification-20260831`, HEAD
  `dfd705afade90f8419c73fff210b17947f433d15`, with existing dirty JSONC/CEP work.
- Main `d8aef5ff2d9ca6e335a355d1d98e07b80aa12300` and experimental
  `b60344f0616104df7dfe8c76f906d1a83ebb5518` remain unchanged. No commit, push,
  Spark synchronization, architecture split, or external-repository change.
- Pre-edit product sources/configs and the pre-edit tracked diff are preserved
  locally in `audit-results/automatic_lcmv_control/baseline_source.tar.gz`.
  SHA-256: `278a3dab696a7930efc518dccd5332a8b6749846bd69134dd5e4f15e81c9e4f2`.
  This is a source/config preservation artifact, not a portable full install.

## Located failure of product intent

The old config `lcmv_test_enabled` initialized the backend state and GUI
checkbox. The checkbox mutated its local config and called the remote worker;
the headless command called `BackendRuntime.set_lcmv_test_enabled`. Automatic
arming also called this same setter. Operator OFF additionally suppressed
automatic arming for the rest of the run. Consequently, deleting only a widget
or a config field would leave other manual entrypoints or break arming.

Two further switches could defeat automatic operation: `lcmv_auto_arm_after_pvt`
disabled automatic arming; `one_run_segmentation_enabled` disabled the health
assessment that updates the required reference. Both were true in the product
profile. Their options and branches are removed; that assessment is mandatory.

## Classification and complete active contract

| Boundary | Outcome and reason |
| --- | --- |
| JSONC/schema | Remove all three switches. No ignored aliases, config mutation, or per-switch rejection shim; normal unknown-key validation applies. All other parsed values remain identical to the preserved profile. |
| Backend | Migrate to private `_lcmv_armed`; remove public enable/disable setter and operator suppression. Inline the now-empty optional-segmentation wrapper into its two callers. |
| Headless/remote worker | Remove the dedicated manual command and client method. Old command names encounter the normal unknown-command handler, regardless of argument type. |
| GUI | Remove checkbox, layout element, builder, configure/toggle handlers and unused import. Keep a read-only protection status and model plot. The feed label names the fixed automatic shared-beam architecture, not an assumed active/nulling state. |
| Automatic lifecycle | Keep health qualification, measured reference, uniform startup, independent jammer gates, hysteretic release, transitions, reactivation and PRN fanout. |
| Current documentation/tests | Migrate configuration, Qt/headless fakes, automatic arming tests and live procedure together. Remove tests requiring the retired manual behavior; preserve their relevant solver/reset concurrency coverage. |
| Historical evidence | Keep dated runs and legacy log parsing in `summarize_lcmv_run.py`, which compares retained runs. The current `lcmv_on` automatic event still feeds its interval builder. |
| Read-only metrics | Retain the actively consumed `lcmv_test` envelope and its `enabled` status field. Here `enabled` means armed, not that null weights are active. GUI/plots/wire serialization/evidence consume this contract; it is not a writable manual interface. |
| Other controls | Start/Stop, MUSIC source-count selection, and optional physical RF-state markers remain. RF markers label observations and do not arm protection or command transmitters. |

## Implemented state sequence

```text
New run: uniform, unarmed, reference collection
  -> healthy GNSS + stable/fresh measured reference: automatically armed, uniform
  -> power/covariance activation evidence + accepted solve: measured-U1 protection
  -> sustained valid lower evidence: uniform recovery, still armed
  -> new activation evidence: protection again using the frozen reference
```

Healthy PVT, enough observations and adequate C/N0 remain required. This does
not introduce cold-start-under-jamming support. A jammer that prevents learning
a healthy baseline can still prevent arming. Same-run reactivation does not
relearn the desired reference. Stop/Start begins a new run and relearns it.

`_maybe_auto_arm_lcmv_after_pvt` now validates/copies the reference under the
results lock while holding the LCMV control lock. It rejects an already armed
state, an already requested stop, missing health flags, unstable/mismatched or
stale/future-dated references, empty/zero/non-normalizable vectors, invalid
matrix shape/non-finite covariance and invalid/insufficient confidence.
Accepted copies own their storage. Arming publishes uniform weights and
unavailable protection, not a null. Reset uses the same control lock as solver
publication and clears the healthy/frozen reference and protection state.

The control lock does not synchronize every GNSS/DoA snapshot or hardware
event. The tests below exercise named schedules, not all possible races.

## Verification chronology

Commands run from the laptop repo using its `.aj` environment. Pytest runs use
`PYTHONDEVMODE=1 PYTHONWARNINGS=error` unless explicitly described otherwise.

1. Baseline: `.aj/bin/python -m pytest -q -m 'not usrp' --tb=short --maxfail=3`
   gave **469 passed, 2 failed, 1 deselected in 16.10 s**. An earlier tool result
   was truncated; this observed repeat is the recorded baseline.
2. Before implementation, plain pytest on `tests/test_automatic_lcmv.py`
   (without development/warning environment overrides) gave **5 expected
   failures**: three switches remained accepted/present, manual setter existed,
   automatic-only manifest was absent. The initial harness first had a missing
   logger key; after correcting the harness, all five failed for intended
   contract reasons. No product correction was made to obtain those failures.
3. Migration focused gate initially gave **173 passed, 5 failed**: four stale
   GUI-label expectations and a reference-free fixture whose new, seeded
   reference changed the inferred run state. The latter was migrated to a
   consistent healthy vector/angle; no production classifier was relaxed.
   Next intermediate gate: **177 passed, 1 failed**, before the fixture's
   angle was corrected.
4. New full-cycle harness initially omitted required `target_selection_source`.
   After fixing its real API call, a near-zero float32/float64 accumulation
   difference exposed an overly strict default comparison. It now uses the
   same complex64 input and explicit `atol=2e-6, rtol=1e-5`; this bounds the
   tested numerical comparison, not physical suppression. No solver was changed
   to satisfy it. Focused config/GUI/headless/beamforming/default suite then
   passed **200 tests in 10.61 s**.
5. Broader checkpoint: **497 passed, 2 baseline failures, 1 deselected in
   15.19 s**. Added invalid-vector normalization and actual wire-status
   regressions afterward. Expanded focused gate passed **242 in 11.45 s**.
6. After wrapper removal and final source review: expanded focused suite
   **242 passed in 11.58 s**. Full non-USRP warnings/development coverage gate
   **499 passed, 2 unchanged baseline failures, 1 hardware test deselected in
   22.01 s**, **79%** aggregate line coverage. Exact final output is retained
   locally in `audit-results/automatic_lcmv_control/final_coverage_output.txt`.
7. Repeated the concurrent-arm, solver/reset-barrier and two-cycle FIFO tests
   ten times in fresh pytest processes: **3 passed per repetition**, 30 passes
   total, with 54 other tests deselected per repetition. Runs took 0.22–0.25 s.
8. `.aj/bin/ruff check .`, `.aj/bin/vulture src tests --min-confidence 90`,
   `.aj/bin/python -m compileall -q src tests`, and shell syntax checks of
   `run_realtime.sh setup.sh tools/run_realtime_sidecar.sh` passed.
   Scoped diff whitespace check passes excluding the unchanged launcher;
   the full check reports only its pre-existing line-10 whitespace.
9. Final non-coverage full suite with `--junitxml` reproduced **499 passed,
   the same 2 baseline failures, 1 deselected in 14.93 s**. Per-test results
   are retained in `audit-results/automatic_lcmv_control/final_tests.xml`.

Final tested source SHA-256 values:

- `src/antijamming/runtime/backend.py`:
  `5c8e29eb1ffd28c6a830ddab8a4461be22ed539ed34ad192029574ac2f0a0072`
- `src/antijamming/ui/main_window.py`:
  `3b555b07485ccf455de49ea46946aaf69eec5d2ba064a2ac3c41e784f1bae45a`
- `configs/antijamming/x300_realtime.jsonc`:
  `1da724dad2f20b4aa39ac9b24871079fc181537a3407c6e5987ca52da0478bb4`

The standalone AST/profile comparison harness initially omitted `PYTHONPATH=src`
and could not import the project. With that environment set, it verified the
stated comparisons. The harness import mistake did not involve a product edit.

Focused modules:

```bash
PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q \
  tests/test_automatic_lcmv.py tests/test_beamforming_output.py \
  tests/test_gui_status.py tests/test_headless_ipc.py \
  tests/test_main_defaults.py tests/test_shared_u1_phase_compensation.py \
  tests/test_runtime_evidence.py tests/test_lcmv_summary_script.py --tb=short
```

New deterministic coverage includes all health gates, reference validation,
copied-storage ownership, three reset/relearning cycles, stop-request guard,
two concurrent arming calls publishing one event, reset waiting for an
in-flight measured solve, automatic status through JSON wire serialization,
and two synthetic jammer activation/release/reactivation cycles. The latter
uses actual runtime/solver/phase-bank code with synthetic IQ and GNSS health;
FIFO slots are unassigned there. Existing phase-bank tests separately exercise
assigned-PRN behavior. Neither test runs real GNSS-SDR or an OS FIFO consumer.

## Comparison and review boundaries

- Targeted source/caller/schema/test/docs review; not every repository symbol
  or every historical audit was reread. Selected instructions/playbook/skill and
  the living tracker were read completely. Truncated outputs were reread where
  needed; source searches are not described as exhaustive manual review.
- AST comparison to the saved backend found jammer activation/release logic,
  FIFO matrix preparation and uniform fallback unchanged, ignoring docstrings
  and the state attribute rename. The measured solve path differs only by
  that rename and a direct call replacing the removed forwarding wrapper.
- Shared-U1 phase-bank file is byte-identical to baseline, SHA-256
  `81a232a18402e8cab131591005c0f081d70a3a458220a8d269a0975977a36ec0`.
- Parsed profile comparison removed exactly the three named keys and found all
  remaining values identical, including the user's current LO export setting.
- Baseline gate failures remain unrelated/unmodified: `run_realtime.sh` calls
  `rey.search`; channel-2 LO export is false in the current profile while its
  historical default-map test expects true. `git diff --check` also reports
  pre-existing trailing whitespace at launcher line 10.
- Existing malformed-covariance conversion can bridge the jammer release hold;
  this remains open in [the latch audit](jammer_latch_audit_2026-09-01.md).
  Automatic-control removal does not repair or conceal it.
- Hardware RF/USRP, real receiver tracking/PVT, arbitrary schedules, temperature,
  external TramiqSDR consumers and long-duration physical jammer trials are not
  verified. Removal is not a general correctness or race-freedom claim.
