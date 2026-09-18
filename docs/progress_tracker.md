# Implementation and Verification Progress

This is the repository's single living progress ledger. Add timestamped entries
here instead of creating another progress-tracker or current-state audit. Exact
run-specific evidence remains under `docs/audits/`; this file summarizes and
links it without rewriting historical observations.

## Claim boundaries

- Cleanup baseline: `main` at
  `0672377aa6c3fa53a11e09747e0cbd300c815579`.
- Active cleanup branch: `cleanup/no-usrp-verification-20260831`.
- `main` and `per-prn-fifo-experimental` are outside this branch's mutations.
- The September 7 measured-only removal is laptop/software-verified, not
  Spark/hardware-verified. Historical attached runs exercised the old archived
  trees; rewriting history does not make those runs evidence for new code.
- A passing test proves only its named inputs and exercised schedules. It does
  not prove that the repository is generally correct or race-free.
- Architecture splitting, including `runtime/backend.py`, is deferred.

### 2026-09-18T09:43:41Z — development archives are not production logging

- User explicitly clarifies that per-run archives belong to development, not
  normal production operation. Recorded this as the GUI/headless product
  requirement in `docs/realtime_gui.md`, replacing the earlier ambiguous
  proposal to merely bound retained-run storage.
- Current cleanup code does not implement this distinction: one
  `logging_enabled` control gates ordinary saved logs and run-session evidence
  together. Setting it false also disables saved errors; that is not the
  requested current-run-only logging behavior. Preserving test provenance is
  not permission to retain every production run indefinitely.
- Documentation only: no runtime/configuration change, branch switch,
  deployment, new test claim or existing-log deletion. Separation remains
  pending; tests of the old archiving behavior do not certify this requirement.

### 2026-09-18T09:40:54Z — origin of automatic run-log archiving traced

- Current history introduces `RuntimeLogSession`, `_copy_session_artifacts`,
  `finalize_session_logs`, crash recovery and `logs/runs/<session>` in
  `7adbdb406b23b4dc5c4dbbb64a78ffe7cf4037a2`, dated August 16 at 06:29:44
  +05:00, subject `fix: harden measured-U1 LCMV continuity`. The preserved
  pre-subject-rewrite commit `6242d36` has the same date and explicitly names
  run evidence. This predates the cleanup branch; main, cleanup and the
  per-PRN experimental branch all contain this ancestor.
- Its parent logging helper truncated named current logs without creating
  these run archives. It explicitly left separate helper/GNSS artifacts
  intact; this is not proof that every old artifact was previously removed.
- The same commit's `docs/15_self_run_live_evidence.md` explains retaining
  Start-to-Stop evidence and recovering unfinished runs before truncation.
  That is an implementation rationale, not proof of the user's approval of
  indefinite retention. No explicit instruction to retain every runtime run
  forever was identified in the available conversation. Git author identity
  is not evidence of who requested or personally implemented the change.
- Retained the introducing patch, parent helper and containing-branch list
  under `audit-results/tramiq-log-review.VcftQC/`. This is historical source
  inspection only; no code, configuration, history or existing logs changed.

### 2026-09-18T09:38:33Z — tramiq retains old anti-jamming run logs too

- Read-only SSH inspection resolved `tramiq` to `spark-7e4d.local`, user
  `tramiq_sdr`. Its home anti-jamming checkout is clean on the cleanup branch
  at `6b45b24b43d2e215c687ffabe185e679be206d08`. The NADS 2-style installed
  `.local/share/tramiqsdr/runtime/antijamming` path is absent there.
- The checked-in realtime profile has `logging_enabled=true`. GUI and headless
  use that setting; backend Start calls `reset_session_logs` when enabled,
  and finalization archives the run. The complete 370-line logging helper
  retains `logs/runs/` directories; truncating current files is not deletion
  of saved copies. Turning logging off would not remove existing history.
- Measured home checkout `logs/`: 29,871,448 KiB, about 30.6 GB (28.5 GiB).
  Separately, `logs/runs/` reports 26,349,336 KiB and contains 117 directories.
  These are existing storage observations, not a current write rate or proof
  that a particular receiver process is running. No receiver or new helper
  test was executed on this host, and no remote state was changed/deleted.
- Evidence: `audit-results/tramiq-log-review.VcftQC/`, including actual helper
  source (SHA-256 `ca6b0db3f2a45421ec28e8d16320aa00137e55088f6144c1e8b29c0e7eb8f0c4`),
  backend call sites, config matches, repository identity and size/count output.
  Latest-only log storage is not the implemented policy; no policy correction
  is claimed or authorized by this explanatory check.

### 2026-09-18T09:33:34Z — installed anti-jamming log lifecycle tested safely

- At the user's request, ran the actual NADS 2 installed logging helper in
  a fresh temporary directory, not against existing operator logs. Two
  simulated receiver-run cycles exercised all 16 named log files: Start
  cleared seeded/current bytes, Stop preserved byte-identical archives,
  the next Start retained the previous archive, and idle logger setup did
  not clear the current files. All six checks passed, including the seeded
  negative control. No GUI, IPC, USRP or GNSS-SDR execution was involved.
- Fresh installed anti-jamming `logs/` allocation: 281,656 KiB (about 275 MiB),
  with 60 run directories. `runs/` alone reports 279,320 KiB. Three tracking
  files also have compatibility hardlinks outside `runs/`, so separately
  measured subdirectories must not be added as independent disk usage.
  The 117,488,317 shared payload bytes are not duplicate physical copies.
- This establishes reset-versus-retention behavior of the installed helper,
  not the cause of the user's historical NADS 1 eight-GB observation or GUI
  lag. Production code/configuration and all existing logs remain unchanged.
  Test artifacts were copied to the laptop. No retention fix is implemented.
- Evidence and exact command:
  [installed-log lifecycle test](audits/launcher_lo_review.md#installed-anti-jamming-log-lifecycle-test--september-18),
  `audit-results/nads2-log-lifecycle.Njs3AF/`.

### 2026-09-18T08:51:08Z — stored UHD 4.6 HG image verified byte-for-byte

- Flash readback completed with exit 0. `cmp -n 11443736` passed against the
  official UHD 4.6 HG bitfile: every source-image byte matches stored flash.
  The larger loader readback has SHA-256
  `e6507c11ab1af27ceedf77b3f5058465ba21e6c0c9f2d336f0a14c99c6804904`.
  The previous flash backup remains preserved on the laptop.
- User was told the write/readback are complete and the USRP can now be
  power-cycled. Stored-image equality does not prove the active image changed;
  post-restart UHD 4.6 initialization remains pending. No host driver, receiver
  configuration, logging behavior or setup script was changed.
- At 08:52 UTC a bounded debug probe still reads active image 39.3 / `d375d68`
  and reproduces the Radio mismatch. Thus the verified new flash image has not
  yet become the active FPGA image. This is not a failed flash-byte comparison;
  physical power-cycle and post-activation verification remain necessary.

### 2026-09-18T08:47:12Z — UHD 4.6 HG written; independent readback underway

- Saved the prior flash image locally and verified the copied backup hash.
  The targeted write to X300 `35D068D` ran 08:41:34–08:46:59 UTC using the
  official 4.6 image and `verify`; loader returned 0 and successful completion.
- This changes the image stored in USRP flash, not the host driver, receiver
  source or `setup.sh`. The loader requests a power cycle for activation.
- A separate readback-only operation is now running. It will compare every
  one of the official bitfile's 11,443,736 bytes with the stored image. No
  byte-comparison or post-activation success is claimed before those finish.

### 2026-09-18T08:30:11Z — authorized UHD 4.6 FPGA load cannot reach the USRP

- User explicitly requests the FPGA image matching UHD 4.6 on the USRP, with
  `setup.sh` corrections deferred. This authorizes the targeted FPGA update,
  not a branch promotion or unrelated NADS 2 application/logging edits.
- Reconnected to `nads2` (`qvise@192.168.3.127`, `spark-8219`) and reread its
  repository instructions. Both Ethernet interfaces report no link; no USRP
  was found by fixed-address or broadcast discovery (both exit 1). The bounded
  UHD 4.6 initialization probe returned No devices found (exit 255).
- No image-loader operation was invoked: current device identity cannot be
  established and the network prerequisite is absent. Recorded loader/probe/
  discovery/library/image hashes and the unchanged remote setup hash in
  `audit-results/nads2-uhd46-flash.kNajmY/preflight.txt`. Loading, reboot and
  post-load compatibility verification remain pending a working Ethernet link.
- Local setup hash is unchanged at
  `9ee6fe8ee4b9a016272cd53295a42b058c5fb8a0b1cec9036697a86f0e47c851`;
  existing JSONC edits predate this request and were preserved. No setup run,
  package installation, network modification, remote source edit or push.
- Additive logging question answered from retained producer/consumer sources:
  backend current logs reset on a new accepted run, but controller/launcher
  captures append and archives persist. The installed copy lacks the cleanup
  logging switch. An installation on this host does not establish where the
  historical build was performed. No new live Start/Stop test was conducted.

### 2026-09-18T07:34:19Z — NADS 2 controller/deployment investigation opened

- User requests actual TramiqSDR headless log behavior, lag diagnosis, a laptop
  SSH alias, and continuation of operator/developer UX principles. Inspect the
  deployed installation separately from source checkouts; do not start/stop the
  live GUI or receiver, modify remote code, or delete logs during diagnosis.
- Added laptop `nads2` for `qvise@192.168.3.127`, reusing the verified saved
  host key from this machine's former `.222` address. Existing `nads` remains
  `.154`. No password is stored in SSH configuration or evidence.
- NADS 2 identifies as `spark-8219`. Found desktop/home TramiqSDR checkouts at
  `9e6eed5` with existing dirty edits, a desktop anti-jamming checkout at
  `d8aef5f`, and a separate `.local/share/tramiqsdr/runtime` installation.
  Initial disk usage: installed anti-jamming logs 276 MiB, desktop checkout
  logs 733 MiB, TramiqSDR state directory 42 MiB. The user's older NADS 1
  eight-GB observation is not yet verified or explained by these measurements.
- Initial GUI PID 126889 had high cumulative CPU use and approximately
  5.3 GiB RSS; it disappeared before root-level sampling. We did not terminate
  it. Concurrent developer edits are present; preserve them and distinguish
  inspected source from the exact running build. No lag cause is established.
- Pending: installed/controller reset/append/archive paths, build exclusions,
  current GUI resource and tab-update evidence, bounded operator UX policy.
  Raw command output and source snapshots are retained in
  `audit-results/nads2-controller-z3GJaW/`. No software/RF test or fix claim.

### 2026-09-18T07:08:16Z — headless logging and controller boundaries inspected

- Confirmed service launch appends/creates named logs; accepted receiver Start
  resets current logs; Stop archives then returns the service to idle. GUI and
  headless use the same backend. Archive accumulation remains unbounded.
- Cleanup honors the logging Boolean; local older main logs unconditionally.
  Profile changes require service restart. IPC disconnect alone is not Stop;
  Stop acceptance also precedes actual completion/idle state.
- Current TramiqSDR controller remains unverified: local project not found and
  bounded SSH to qvise `.156` returned `No route to host`. No product edits,
  receiver launches or fresh tests. [Source paths and limits](audits/launcher_lo_review.md#headless-log-lifecycle-and-integration-boundary--september-18).

### 2026-09-18T07:01:40Z — archive-reference importance inspected

- Git-only reachability check: archive/original refs retain 57 historical
  commit identities absent from current branch ancestry; not a claim of
  missing product features. Six archive tips preserve such ancestry. The
  pre-latch main tip equals current main; three original tips duplicate
  archives. No independent backup verified and no reference deleted.
- Display-message removal remains unchanged against its tested source
  manifest. No new tests, runtime changes or publication in this follow-up.
  [Commands and limits](audits/launcher_lo_review.md#complete-git-reference-inventory).

### 2026-09-18T06:52:42Z — desktop-launch scope replaces custom display precheck

- User does not want launcher desktop/offscreen troubleshooting advice.
  Removed its custom precheck, unused helper and shell/Python visibility
  hints. Retained Qt selection, explicit offscreen support, platform file
  logging and failed-child exit propagation. Missing-display failure now
  belongs to Qt, not an early shell exit; headless processing is unchanged.
- Before change: **13 new-contract failures, 9 passes**. After: **59 focused
  passes**; normal and development/warnings coverage full runs each **525
  passed, 1 hardware deselected**, **79% coverage**. Static/syntax/diff gates
  passed. Shell tests use stubs; GUI print/log retention uses AST inspection.
- All four branch tips, runtime profile, headless/backend and UHD helper are
  unchanged. No RF/remote-source/logging-policy/commit/push/package changes.
- Follow-up inventory: four local ordinary branches, three live GitHub heads,
  seven archive refs and three pre-rewrite original refs. Old UHD build branch
  name is retained only in inspected history; extra worktrees are detached.
  No branch/backup pruning. [Evidence and boundaries](audits/launcher_lo_review.md#desktop-only-operator-launcher--september-18).

### 2026-09-18T06:43:36Z — GUI/headless logging retention inspected

- Current profile logging is true. App initialization appends; stream Start
  resets the current named logs; Stop archives and retains them. GUI and
  direct headless share the backend lifecycle. GUI launch additionally resets
  its UHD console and preserves prior optional sidecar output separately.
- No size rotation or run-archive age/count/total-size pruning found in these
  paths. Saved runs can accumulate despite clean current logs. Disabling the
  Boolean also disables saved errors, not only developer chatter; live GNSS
  state parsing remains. **8 focused temporary-directory tests passed**.
- No logging-policy/configuration change, archive deletion or new branch.
  Same-codebase releases with bounded operational logs are a recommendation,
  not newly implemented behavior. [Evidence and limits](audits/launcher_lo_review.md#follow-up-deployment-and-log-retention--september-18).

### 2026-09-18T06:40:34Z — plain-language GUI display messages

- Removed routine Qt platform/display-target prints and replaced both missing-
  desktop errors with plain-language instructions. Kept platform selection,
  display prechecks, exit status, lifecycle and all processing unchanged.
  Technical platform identity remains in the existing GUI log when enabled.
- Documented GUI versus direct headless operation: a headless backend does not
  need Qt offscreen mode. No headless implementation or broad logging redesign
  was made; other runtime messages remain. Product edit is launcher-only.
- Nine new message assertions failed against the previous launcher; after the
  edit, **55 focused tests passed**. Full software and warnings-as-errors/
  development coverage runs each passed **521 tests, 1 hardware deselected**,
  with **79% coverage**. Ruff, Vulture >=90%, compileall, six shell syntax
  checks and diff check passed. Launcher tests use stub external boundaries;
  these results do not prove real display reachability or radio operation.
- Main/experimental refs and GUI/headless/backend/profile/UHD helper hashes
  are unchanged. Previous FIFO/latch/calibration/ownership findings stay open.
  No remote change, hardware run, commit/push or archive refresh. Evidence and
  exact commands: [display-message audit](audits/launcher_lo_review.md#operator-facing-display-messages--september-18).

### 2026-09-18T06:07:25Z — two smaller launcher candidates removed with bounded evidence

- Removed only the launcher's default `MPLBACKEND=Agg` injection; replaced
  the historical desktop-address hint with machine-independent guidance.
  Retained Qt configuration and missing-display rejection, inherited user
  settings, lifecycle safeguards, Matplotlib dependency and vendored plotting
  tools with concrete source consumers. No dependency was uninstalled.
- New regression previously failed twice on the old launcher. After the edit:
  **47 focused tests passed**. Full software selection under a validated
  Matplotlib import blocker: **513 passed, 1 hardware test deselected**, with
  no Matplotlib attempts or loaded modules in that test interpreter. Normal
  and warnings-as-errors/development coverage runs also each **513 passed,
  1 deselected**; coverage **79%**. Child processes and unexercised optional
  paths are not covered by the import hook. No actual receiver/hardware ran.
- Ruff, Vulture >=90%, compileall, six shell syntax checks and diff check
  passed. Baseline transformation assertion limits the product change to
  those two launcher locations; requirements/profile/backend hashes and all
  three branch tips remain unchanged. Existing user edits were preserved.
- The earlier FIFO latency failure did not recur here but remains undiagnosed;
  display cleanup does not fix it. Other setup/shutdown/calibration/jammer
  findings remain open. No commit/push, Spark sync or tar.gz refresh.
- Exact commands, retained failed controls, source snapshots and boundaries:
  [scoped stale cleanup](audits/launcher_lo_review.md#scoped-stale-launcher-cleanup--september-18).

### 2026-09-18T06:03:39Z — scoped launcher stale-removal verification opened

- User authorizes removal of the two smaller candidates only where current
  consumers do not need them. Cleanup worktree remains at `dfd705a` plus
  existing edits; main, experimental, hardware and other repositories stay out
  of scope. Preserve the earlier audit's independent shutdown/setup findings.
- Wider source search found genuine Matplotlib consumers in vendored
  GNSS-SDR skyplot/OSNMA/tracking analysis tools. Retain the package requirement
  and these tools. The product GUI uses PyQtGraph; remove only its launcher's
  default `MPLBACKEND=Agg` injection, without clearing a user's inherited value.
  Replace the machine-specific desktop address with generic display guidance;
  retain the actual missing-display rejection and Qt platform handling.
- Baseline sources, references, hashes, refs and current dirty diff retained
  in `audit-results/launcher_stale_removal_gIwsGY/`. New real-shell regression
  before product edits: **2 failed, 1 passed, 7 deselected** for injected Agg
  and the hard-coded desktop message; explicit inherited backend is a passing
  control. No real GUI/UHD/receiver was launched. Implementation and final
  broader verification pending.

### 2026-09-17T12:45:22Z — audit launcher necessity and script staleness

- User requested explanation/audit, not removals. Read all six project-owned
  Bash scripts and trace their callers; keep the GUI launcher, setup, tests,
  UHD environment/build and optional resource-monitoring roles distinct.
- Reproduced the real launcher PID selector missing a modeled receiver in its
  own process group, with a same-group positive control. Actual producer code
  starts the receiver in a new session. Other cleanup protections still exist;
  no real orphan/hardware shutdown claim follows. Parsed runtime profile also
  confirms setup checks a different calibration filename from the selected one.
- Recorded source-built UHD discovery migration gaps, sidecar session-ownership
  risk, hard-coded desktop hint, Matplotlib removal candidate, active duplicate
  helpers and missing direct tool declarations. Rejected a premature claim that
  the GNSS VOLK profiler path was obsolete after finding its post-build copy.
- Six Bash syntax checks and the software probe passed. Existing launcher/UHD
  environment/JSONC selection: **44 passed**. These do not cover away the newly
  identified problems. Previous full-suite FIFO latency failure remains open.
- Only evidence/documentation changed; no product edit, removal, setup/RF run,
  archive refresh, commit/push or Spark synchronization. Detailed classifications,
  coverage limits, commands and evidence: [launcher/script review](audits/launcher_lo_review.md#follow-up-launcher-necessity-and-stale-script-assumptions).

### 2026-09-17T12:35:01Z — replace cleanup distribution archive

- User requests replacing the previous home-directory tar.gz after the
  launcher/LO corrections. Package this cleanup branch's current working files,
  including uncommitted changes, tests, vendored GNSS-SDR and retained audit
  evidence; exclude Git history, ignored virtual environments/builds/caches
  and live logs. This is packaging, not a new software or hardware test.
- Previous distribution archive:
  `/home/u/antijamming-cleanup-dfd705a-2026-09-15.tar.gz`.
  Replacement destination:
  `/home/u/antijamming-cleanup-dfd705a-2026-09-17.tar.gz`.
  Build and verify the new artifact before retiring the old one to recoverable
  desktop Trash. The nested baseline tar.gz is evidence and must be retained.
- Initial packaging checkpoint: **2,275 files**; gzip integrity, full tar
  comparison with working files, exact selected/member path equality, and
  required latest-file presence all passed. Refresh the artifact after this
  ledger addition and repeat these checks before publication/old-file removal.
  The known jammer-release/calibration findings and FIFO timing failure remain
  explicitly unresolved in the included tracker and review. No new test-suite
  pass, code/configuration change, Git operation or Spark synchronization is
  implied by creating the archive.

### 2026-09-17T17:05:02Z — requested launcher/LO repairs verified at software boundary

- Completed the two scoped corrections recorded below. Focused warnings-as-
  errors/development-mode run: **53 passed**. Full non-USRP run: **509 passed,
  1 failed, 1 hardware test deselected**. Coverage run: the same counts,
  **79% statement coverage**; no general correctness or hardware claim.
- The remaining suite failure is the unmodified 32-source FIFO stress latency
  assertion: 315.15 ms maximum against 250 ms; isolated repeat 599.69 ms;
  coverage run 578.01 ms. All preceding byte-count/hash checks passed in these
  runs. Cause is unconfirmed; no FIFO change, threshold relaxation or hidden
  skip was made. This is a new observed verification boundary for follow-up,
  not evidence that the launcher/LO changes caused a FIFO regression.
- Rechecked the independent review with software fault probes: malformed
  covariance still bridges the release hold; incompatible calibration context,
  NaN uncertainty and string `"false"` quality are accepted; four-channel
  clipping aggregation can hide one channel crossing the per-channel level.
  These remain unresolved. Temporary probe inputs did not edit product files.
- Final Ruff, Vulture (90%), compileall, Bash syntax and diff checks passed.
  Source manifest verification passed after the tests. Runtime profile, selected
  calibration and backend hashes equal the baseline; all branch refs are
  unchanged. No commit/push, Spark sync, RF action, SignalSim edit or archive
  replacement was performed.
- Details, before/after evidence, one corrected auditor-script field-name error,
  exact commands and untested boundaries:
  [launcher and LO review](audits/launcher_lo_review.md). The existing dirty
  automatic-control/CEP/JSONC work was preserved. The saved calibration's old
  export array remains provenance, not something rewritten to satisfy a test.

### 2026-09-17T16:55:22Z — launcher typo and LO regression repair in progress

- Scope: laptop cleanup worktree at `dfd705a` plus preexisting user edits;
  correct the launcher and stale LO test, review the supplied September 15
  report. No main/experimental, Spark, RF, receiver, calibration or profile
  mutations. New attachment is byte-identical to the previously supplied report
  (SHA-256 `fed11df145cec08e2c361d6cd5acc01719faf2d8442b1a8a52a3be0ea0dfa8e4`).
- Before edits, the existing focused selection reproduced **2 failures,
  1 pass**: undefined `rey.search` and the test's stale `[T,F,T,F]` assertion
  against the user's `[T,F,F,F]`. A new real-shell/real-JSONC harness with stub
  external processes then reproduced **3 failures, 3 passes**, including
  application startup before the failing lookup. No real GUI or UHD ran.
- Correction batch: use `re.search`, resolve the sidecar interface before GUI
  startup, and separate Python lookup status from route-command substitution.
  Align the LO test with the intended A-first-channel-only export and exercise
  the actual device-wrapper-to-UHD call boundary using a recording mock.
- The retained phase-calibration record of September 11 at 14:39 UTC includes
  two 20-second A-master `[T,F,F,F]` captures. This supports the intended map,
  not universal superiority or validity of the old `[T,F,T,F]` calibration
  vector. That saved calibration metadata is deliberately unchanged; its
  operating-context validation remains open, as does the jammer-release defect.
- Baseline source copies, refs, hashes, tracked diff and raw failing logs/XML:
  `audit-results/launcher_lo_review_gce0zJ/`. Focused and broader post-change
  gates are pending; no completion claim yet.

### 2026-09-16T07:10:14Z — beginner pipeline and GNSS source walkthrough

- Explained the current laptop working tree at `dfd705a` plus existing edits.
  Read `dsp/pipeline/stages.py`, both DSP/GNSS `models.py` files and GNSS
  `process.py`; traced their backend callers and selected bridge, renderer,
  FIFO, monitor, state, snapshot and shared-phase implementation sections.
- Source/profile distinctions: four radio inputs versus ten GPS receiver
  slots; continuous GNSS publication precedes every-fifteenth-chunk DSP
  selection; the backend orchestrates the separate stage helpers. GNSS models
  hold identifiers/process records, and `process.py` manages executable and
  process lifecycle helpers. The separate root `gnss-sdr/` holds receiver code.
- This is a targeted code explanation, not an exhaustive audit or a fresh
  runtime/hardware verification. No application/configuration changes, receiver
  launches or tests; only this ledger entry was added.

### 2026-09-14T11:58:11Z — third-party TwinRX report cross-check; no fixes applied

- Scope: inspect the user's pasted September 10 report against the current
  laptop cleanup worktree, HEAD `dfd705afade90f8419c73fff210b17947f433d15`
  plus existing uncommitted automatic-control/JSONC/CEP changes. No source,
  configuration, branch, hardware, Spark, or remote changes in this check.
  This ledger entry is the only intentional repository edit. The supplied
  `/mnt/data/twinrx_repo_audit/` and ZIP are absent here, so their claimed file
  inventory and 349-test result were not independently inspected.
- Fresh fault injection reproduces the release exception gap at
  `runtime/backend.py:_update_lcmv_test_from_music_locked`: failed conversion
  bypasses the gate's release-candidate reset. With synthetic arming and high
  evidence first, valid low evidence at 10 s starts the timer; malformed
  covariance at 11 s leaves its start at 10 s; valid low evidence at 12.1 s
  releases protection. A numeric 3x3 covariance control resets the timer at
  11 s and leaves protection active at 12.1 s. This tests state transitions,
  not physical suppression or the incidence of malformed RF covariance.
- Reproduction used `PYTHONPATH=src:tests .aj/bin/python`, actual
  `BackendRuntime`, `StreamConfig(lcmv_weight_transition_s=0.0)`, and the
  existing `_build_loggers` / `_prepare_automatic_reference` test helpers.
  Mock `antijamming.runtime.backend.time.monotonic` to 9 s while arming and
  calling `_lcmv_jammer_activation_evidence` with `10*np.eye(4)` and raw/cal
  average powers 10. At 10, 11, 12.1 s call `_update_lcmv_test_from_music` with
  `np.ones((4,32),dtype=np.complex64)`, both MUSIC angles NaN, powers 1 and
  covariance `[np.eye(4), 'malformed', np.eye(4)]`. Repeat in a fresh runtime
  replacing `'malformed'` with `np.eye(3)`. Inspect protection-active and
  release-candidate fields after each call. No device or receiver process ran.
- Fresh normal-path check: **5 passed, 52 deselected in 0.16 s**:

  ```bash
  PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q tests/test_beamforming_output.py -k 'jammer_release_requires_valid_low_evidence or release_is_evaluated_without_music or automatic_live_contract_arms or healthy_pvt_auto_arms or realtime_lcmv_stays_uniform_for_angle' --tb=short
  ```

- Fresh calibration probes call the real `build_runtime_config()` while
  mocking only `Path.read_text` for the resolved selected calibration file;
  every other file read delegates to the original method. Each probe starts
  from a fresh deep copy of that file, applies the following substitutions,
  and returns `json.dumps(copy)` to the real loader. The original file is not
  modified. Results:

  | Substitution into calibration metadata/vector | Startup result |
  | --- | --- |
  | None | Accepted; complex-gain vector length 4 |
  | `center_freq_hz=100000000.0`, `gain_db=1.0` | Accepted despite mismatched runtime context |
  | `phase_offsets_std_deg=[0,NaN,NaN,NaN]` | Accepted |
  | `quality_pass="false"` | Accepted |
  | `quality_pass=false` Boolean control | Rejected |
  | Complex-gain vector `[[NaN,0],[1,0],[1,0],[1,0]]` | Rejected |
  | Complex-gain vector `[[1,0]]` | Rejected; length 1 rather than 4 |

### 2026-09-14T11:19:12Z — automatic-only LCMV control migration opened

- User requests removal of manual LCMV control. Scope: laptop cleanup branch
  `dfd705a` plus preserved dirty JSONC/CEP work; main `d8aef5f`, experimental
  `b60344f`, Spark, hardware, receiver C++ and other repositories unchanged.
- Located the complete writable contract: JSONC/schema flags -> backend
  enable/disable setter -> headless command -> remote worker -> GUI checkbox.
  The same flag also gates automatic arming and FIFO protection. Removing only
  the checkbox or config key would not remove manual control coherently.
- Remove: manual config/GUI/IPC setter and operator auto-arm suppression.
  Migrate: arming to private automatic runtime state; tests and current guides.
  Retain: healthy-reference qualification, measured-U1 solver, jammer gates,
  release/reactivation, uniform safety, read-only `lcmv_test` metrics envelope
  consumed by GUI/plots/evidence, Start/Stop and optional physical RF markers.
  Historical log parsing/audits remain evidence, not runtime controls.
- Baseline warnings-as-errors/development-mode non-USRP suite: **469 passed,
  2 failed, 1 deselected in 16.10 s**. Failures precede this implementation:
  `run_realtime.sh` calls `rey.search`; checked-in LO export expectation differs
  from the current dirty profile at channel 2 (False rather than True).
  Neither unrelated setting is changed as part of this task. An earlier
  baseline tool output was truncated; this observed repeat is the recorded gate.
- Implemented locally: all three arming/reference-disable switches removed;
  no GUI checkbox/handler, remote-worker setter or headless manual command.
  Arming is private `_lcmv_armed` state. Healthy-reference qualification/copy,
  arming and publication share the control/results locks; a new run resets
  its reference and starts uniform. No numerical threshold was changed.
- 2026-09-14T11:34:18Z checkpoint: focused configuration/GUI/headless/automatic
  arming/DSP/FIFO/phase-bank/runtime-evidence/summary suite **242 passed in
  11.45 s**. Earlier broad gate **497 passed, 2 baseline failures, 1 deselected
  in 15.19 s**; two additional regressions were added afterward. Final gates
  are pending after removing the now-redundant segmentation wrapper.
- Tests exercise health and stale/invalid-reference rejection, once-only
  arming, owned reference copies, three resets/relearning, stop-request guard,
  barrier-controlled solver/reset ordering and concurrent arming; synthetic
  GNSS/IQ -> automatic arming -> measured solve -> actual phase-bank output ->
  two release/reactivation cycles; wire status serialization and read-only GUI.
  They do not run an actual USRP or GNSS-SDR process.
- The previously reproduced malformed-covariance release-hold gap remains
  open. No claim of hardware correctness or exhaustive absence of races.
  Exact migration/verification provenance is in
  [automatic-control audit](audits/automatic_lcmv_control.md).
- 2026-09-14T11:38:59Z final source checkpoint: **242 focused tests passed in
  11.58 s**; complete
  non-USRP warnings/development coverage gate **499 passed, the same 2 baseline
  failures, 1 hardware test deselected in 22.01 s**, 79% aggregate coverage.
  Ten fresh-process repetitions of the two controlled concurrency cases and
  synthetic two-cycle FIFO test each passed all three cases. Ruff, Vulture,
  compileall and shell syntax passed; the scoped diff check passed, while the
  full check retains the pre-existing launcher whitespace finding.
- Final non-coverage repeat: **499 passed, the same 2 baseline failures,
  1 deselected in 14.93 s**; per-test JUnit evidence is retained alongside
  the baseline archive. No source changes followed the tested source hashes.
- Automatic-control migration is software-verified under those named inputs;
  no new regression appeared in the exercised paths. All three Git branch tips
  remain unchanged; changes are local/uncommitted. The source/config baseline
  archive, tested hashes, exact output, intermediate test-fixture failures and
  final limitations are retained in the linked audit. Hardware verification
  and the existing malformed-covariance hold gap are not claimed completed.

### 2026-09-14T10:28:00Z — remove configured PVT truth; CEP migration opened

- User requests removal of truth-position code/config/metadata in favor of
  CEP. Scope is laptop cleanup branch dfd705a plus existing uncommitted JSONC
  work; main d8aef5f and experimental b60344f remain unchanged. No architecture
  split, GNSS-SDR source change, Spark sync, commit or push is included.
- Actual baseline: accuracy.py calculates every CEP radius about the configured
  truth, and the GUI requires truth_available. Removing only three config keys
  would therefore disable CEP. Proposed replacement is cumulative horizontal
  scatter about the run's measured mean, explicitly repeatability rather than
  absolute position accuracy, with at least two fixes before publishing radii.
- Remove: three gnss_truth_static fields, validation/initialization/warning
  paths, absolute ENU/3D/window errors, and active log/payload consumers. Migrate
  CEP calculation, UI readiness/description, tests and current documentation.
  Preserve dated audits and old run evidence; operator RF ON/OFF markers and
  antenna/LO calibration metadata are unrelated to configured PVT truth.
- Baseline command, before any product edit: PYTHONDEVMODE=1 PYTHONWARNINGS=error
  .aj/bin/python -m pytest -q -m 'not usrp' --tb=short --maxfail=3:
  451 passed, 1 failed, 1 deselected in16.93s. Failure is the existing
  run_realtime.sh embedded reader calling rey.search instead of imported re;
  test_real_shell_profile_readers_accept_comments_without_site_packages exposes
  it. It is not caused by the upcoming CEP changes. Preserve existing edits;
  do not claim a clean baseline. Focused migration and broad gates pending.
- Implementation now removes all named position-truth fields/branches,
  initialization, absolute-error outputs and active consumers. New CEP uses
  WGS84 ellipsoid-surface ECEF projected into the first fix's local horizontal
  plane, centered on the complete run mean. Height does not contribute.
  Nearest-rank50/95 radii retain the existing keys with explicit run_mean and
  horizontal_repeatability metadata. Minimum2 is enforced in profile loading;
  one fix remains warming. This is not absolute accuracy or a moving-worldwide
  trajectory metric. Config comments and current diagnostics guide explain it.
- Full producer/consumer trace: monitor protobuf validation -> cumulative
  state-locked point list -> accuracy builder -> snapshot -> unchanged generic
  serialization -> GUI; receiver events and both backend status log formats;
  compact runtime-evidence allowlist. No receiver C++/FIFO/beamforming/LO or
  calibration settings were changed. Truth-free CEP cannot measure common
  position bias; old hardware runs are not evidence for its new semantics.
- New contract tests failed on the prior implementation:3 failures before
  maxfail, proving the fields existed and old config accepted them. After
  migration, first focused run had261 passes/1 failure: the comment-layout test
  only supported single-line comments. It now permits a contiguous wrapped
  comment block while retaining spacing/attachment checks. Second focused
  run:264 passed in12.01s with warnings-as-errors/development mode. Tests include
  empty/single fixes, complete-run recentering, nearest-rank radii, dateline,
  poles, height exclusion, invalid coordinates, removed-config rejection,
  real bridge->serialized GUI consumption, stale display and log formatting.
- Source/config/tools search leaves no named static-position-truth/error
  producer or consumer. Remaining physical_switch_truth_available describes
  independent operator RF-state evidence and is intentionally retained;
  AGNSS assistance, receiver-measured coordinates and phase-calibration metadata
  are also separate implemented consumers, not authored PVT accuracy truth.
  Test-only retired names assert absence/rejection. Historical audits retained.
- Ruff caught the new helper's missing explicit zip strictness; added
  strict=True for equal-length east/north lists. Final gates follow that edit.
  The pre-existing run_realtime.sh also has trailing whitespace on line10;
  neither it nor rey.search is silently removed by this scoped CEP change.
- Final September14 10:42UTC gates after strictness edit:
  full warnings-as-errors/development-mode non-USRP suite470 passed,1 failed,
  1 hardware test deselected in15.96s. Coverage run under the same settings:
  470 passed,1 failed,1 deselected in30.06s;79% aggregate,95% accuracy.py.
  Both failures are the same pre-existing rey.search launcher typo from the
  baseline; the new CEP paths have no failing test in these exercised gates.
  Ruff check . and Vulture src/tests >=90% returned0; compileall and diff
  whitespace checks restricted to src/configs/tests/docs returned0. Whole-tree
  diff check still reports the pre-existing launcher line10 whitespace.
- Additional runnable contract harness constructed AccuracyMixin with two
  received fixes and fed its result into BackendRuntime._maybe_log_gnss_snapshot
  using an in-memory logging.StreamHandler. Both the quality-transition and
  runtime-snapshot messages rendered cep_reference=run_mean plus CEP50/95,
  with no truth fields or logging-format error. No USRP/receiver process ran.
- Final SHA256: accuracy.py
  29ae818a9b97efdc8d3023d081fddf12fd9d8ecd30da90b8b1e52df1cd4cb8b2;
  x300_realtime.jsonc
  91a21f82f31f8a312a51653178504671232ee65ec6f28d9c210c40b05994fc29;
  new regression module
  9d46a15781ab000ba15ef13e6a934b86f9cb6ad39e979197946c9d16921ed14f.
  Reread changed producer/consumer sections after edits; this is targeted
  contract review, not every line of the repository or proof against all races.
- Status: scoped truth-position removal/CEP migration implemented locally,
  software-verified under the stated tests. Existing launcher defect, physical
  receiver validation, external TramiqSDR consumers and long-run cumulative
  memory/cost remain outside the proven boundary. Main/experimental hashes
  remain exactly as at baseline; no commits, pushes or Spark synchronization.

### 2026-09-10T17:33:59+05:00 — UHD 4.6–4.11 FPGA and tool comparison

- Scope: upstream-source and local-installer audit requested before allowing
  an unversioned latest-Ettus update. Laptop cleanup remains `dfd705a` plus
  the existing user-owned edits; main remains `d8aef5f`. The condition that
  compatibility is unchanged is false, so no automatic-update implementation,
  package installation, source build, FPGA operation, or remote change follows.
- Obtained the official `EttusResearch/uhd` release tags in a disposable bare
  partial clone at `/tmp/uhd-compat-audit.nfoc30/uhd.git`. Exact tag commits:

  Host requirements mechanically extracted from the six tags:

### 2026-09-08T16:58:59+05:00 — reusable commenting instructions

- User requested the learned commenting/layout rules across file types. Added
  one shared section to `/home/u/AGENTS.md`: hierarchy, exact blank-line and
  inline spacing, width/placement, language/formatter exceptions, substantive
  explanations, factual provenance, stale-note review and proportional checks.
  Existing shared safety/identity/verification instructions are preserved.
- Added `/home/u/.codex/AGENTS.md` as a small instruction to read that canonical
  file, not a duplicated style guide. The OpenAI Docs skill led to checking the
  [documented global/project discovery boundary](https://learn.chatgpt.com/docs/agent-configuration/agents-md):
  a file above the Git root alone is not the documented global entry point.
  Both the default global AGENTS and its override were absent before this edit;
  CODEX_HOME was unset. No global configuration setting or other machine changed.
- Verification: reread both instruction files completely; confirmed the old
  shared guidance is an exact unchanged prefix, checked trailing whitespace
  and `git diff --check`, and parsed the extracted Python example with
  `ast.parse`. All passed. A no-index diff returned the normal status 1 for a
  new file with no whitespace diagnostics; the explicit whitespace check was
  run on both files independently of that status. No new agent session was
  spawned, so future instruction adherence is not claimed as experimentally
  proven. This is instruction/documentation-only work; prior software-test
  results are not fresh runtime/hardware evidence for this edit. The realtime
  profile hash remains `eda59d3222528024aeff81c1969e5ce437530e02ff85b694562d2ebbc9f96e10`.

### 2026-09-08T16:52:35+05:00 — JSONC comments and coordinated readers

- Scope: uncommitted laptop cleanup work based on `dfd705a`; supersedes the
  underscore-metadata presentation and invalid `//hello` state recorded below.
  The user confirmed that the filename and all readers must migrate together.
  The sole realtime profile is now `configs/antijamming/x300_realtime.jsonc`.
  There is no duplicate `.json` profile or long new configuration guide.
- Replaced metadata strings with actual comments: 8 equals-sign major banners,
  26 lighter subsection headings, two blank lines before major sections, and
  short explanations. Subsequent spacing feedback is implemented as blank
  separation around above-setting comment blocks, never between a comment and
  its setting. Inline notes are retained only when the complete line fits in
  88 characters; longer notes move above. Short inline groups remain compact.
- Implemented a stdlib-only comment masker/parser in `antijamming/jsonc.py`.
  It preserves quoted strings, escapes, source offsets and newlines; allows
  line/block comments; rejects duplicates, non-finite floats, malformed strings,
  trailing commas and tokens joined by comments. The application's existing
  recursive numeric validation remains intact, including oversized integers.
  Review caught that the first parser draft had unnecessarily removed that
  validator; it was restored and its huge-integer boundary regression-tested.
- Active contract traced: schema default -> application loader; both embedded
  readers in `run_realtime.sh`; sidecar address reader; setup address writer.
  All use the shared JSONC parser. Shell import paths are set before reading.
  Setup masks comments when locating the real address and edits the original
  source atomically, preserving comments, transport arguments and file mode.
  Tests execute the actual extracted shell Python with `-S` and temporary
  profiles, not copied reader implementations or live setup/hardware actions.
- Updated tests, app/schema filename references and four current docs. Searches
  found no old `.json` path in active src/tools/tests/launchers/current docs;
  historical audits and earlier ledger entries retain their original filenames.
- Baseline comparison against `git show HEAD:configs/antijamming/x300_realtime.json`
  passed for all **124** keys, values, JSON value types and complete derived/
  anchored runtime settings. Focused tests also establish identical rendered
  GNSS configuration after comment removal, including ten sources and disabled
  source synchronization; no receiver process is started.
- Initial final-format focused run: **220 passed in 2.88 s**. A broader run
  returned **13 failed, 439 passed, 1 deselected in 16.58 s**: failing GUI reads
  saw a transient literal `some` before the first comment. A subsequent read
  had the expected content/hash again; no source correction was applied for
  that transient result. Its input origin is not asserted. Avoid moving editor
  focus during live user input. Recheck including GUI: **269 passed in 13.31 s**;
  broader rerun: **452 passed, 1 deselected in 17.13 s**.
- The pre-final-spacing coverage rerun passed **452 tests, 1 deselected in
  26.36 s; 79% aggregate**. Its before/after profile hash stayed
  `e0e75a3b140013d0dd46f88677d175c4330f38e38fdcaee850aa78387466afd1`.
- After the final block-spacing pass, the 124-value/derived-settings comparison
  passed again. With `PYTHONDEVMODE=1 PYTHONWARNINGS=error`, focused command
  `.aj/bin/python -m pytest -q tests/test_jsonc.py tests/test_main_defaults.py
  tests/test_gnss_sdr_config_template.py tests/test_gnss_sdr_bridge.py
  --tb=short --maxfail=3` passed **220 tests in 2.84 s**. It checks the actual
  banner/subsection spacing, per-setting notes, inline width, parser failures,
  runtime equivalence, rendered receiver config and shell reader/writer paths.
- Final broad gates, again with development mode and warnings-as-errors:
  `.aj/bin/python -m pytest -q -m 'not usrp' --tb=short --maxfail=3` passed
  **452 tests, 1 hardware test deselected in 22.80 s**. Repeating with
  `--cov=src/antijamming --cov-report=term` passed **452 tests, 1 deselected in
  30.52 s; 79% aggregate source coverage**, including 100% statement coverage
  of the new comment parser (not proof of every possible input).
  `.aj/bin/ruff check .`, `.aj/bin/vulture src tests --min-confidence 90`,
  `.aj/bin/python -m compileall -q src tests`, individual `bash -n` invocations
  for `run_realtime.sh`, `setup.sh` and `tools/run_realtime_sidecar.sh`, and
  `git diff --check` passed. Final profile hash stayed:
  `eda59d3222528024aeff81c1969e5ce437530e02ff85b694562d2ebbc9f96e10`.
- Editor screenshot inspection confirmed the file is recognized as "JSON with
  Comments" and the earlier red comment-syntax message is absent from the
  displayed profile. This does not claim all workspace diagnostics vanished;
  no editor settings, spell-check configuration or Python diagnostics changed.
- Main (`d8aef5f`) and experimental (`b60344f`) remain unchanged. No commit,
  push, Spark synchronization, tarball update, hardware test, receiver rebuild,
  RF/beamforming/latch change or other-repository mutation. Software checks
  do not establish hardware behavior, physical nulls or absence of races.

### 2026-09-08T16:15:00+05:00 — explain the realtime profile without changing behavior

- Scope: laptop cleanup branch at `dfd705a`. Added comments for all **124**
  authored runtime settings, organized into **8 sections / 26 subsections** in
  `configs/antijamming/x300_realtime.json`. These are valid JSON string entries
  named `_section_*`, `_subsection_*`, and `_comment_<setting>`; the existing
  loader already ignores top-level underscore metadata. No parser, setting
  name, setting value, runtime implementation, template or launcher changed.
  Preserved the earlier uncommitted September 7 confirmation entry below.
- Traced the schema/strict loader through `app/runtime_config.py`, UHD device
  arguments/LO polling, backend scheduling/health/arming/evidence/safety paths,
  Shared-U1 desired-vector/compensation code, and GNSS bridge renderer,
  process, UDP/NMEA and accuracy consumers. For conditional receiver options,
  inspected bundled C++ acquisition configuration/PCPS, DLL/PLL tracking and
  assistance-time initialization. This was targeted producer/consumer review,
  not a manual reading of every runtime or vendored-source line.
- Important implementation distinctions now beside the actual settings:
  `lcmv_test_enabled` is the retained on/off/armed control, not the removed
  ideal/measured selector; auto-arm starts it off and requires healthy PVT and
  measured-reference checks. `one_run_segmentation_enabled` also enables
  healthy-reference updates, not just logging. `array_design_freq_hz` is
  manifest metadata while steering uses `center_freq_hz`. Executable discovery
  uses build/install paths, not `gnss_sdr_repo_dir` alone. White-noise-gain
  rejection is disabled in the live measured path; narrow tracking settings
  are inactive with extension=1; accuracy-window count is a cumulative-statistic
  minimum. The clipping fraction aggregates across channels/reporting interval.
- Documented the existing malformed-covariance release-hold gap directly beside
  `lcmv_jammer_release_hold_s`, linking the retained jammer audit. This edit does
  not repair that gap or establish physical jammer classification. Operational
  receiver stdout/UDP/NMEA are explicitly distinguished from optional log
  persistence: the bridge uses them for state/charts/continuity.
- `git log --diff-filter=A -- configs/gnss-sdr` traces its template and local
  assistance XML to baseline `f30234d` (2026-07-01). This folder was not newly
  introduced by cleanup: it contains receiver configuration, not another
  receiver checkout. `config_renderer.py::_render_config` uses the template to
  produce the PID-scoped runtime `fifo_gps_l1.conf`; XML assistance is currently
  disabled. The customized receiver source/build is under root `gnss-sdr/`.
- Added regressions for per-setting adjacent comment coverage, consecutive
  nonempty sections/subsections, identical loaded dataclass values before/after
  stripping metadata (including anchored paths), and byte-identical GNSS-SDR
  config rendering with the same controlled runtime/PTY paths. The latter
  asserts ten sources and `synchronize_signal_sources=false` without starting
  hardware or a receiver process.
- One-time JSON comparison against `git show HEAD:configs/antijamming/x300_realtime.json`
  established all 124 setting keys, values **and JSON value types** unchanged.
  A mock-path negative control ran the new annotation-coverage test on that old
  profile: it failed as expected, while the annotated profile passed. The first
  standalone harness lacked the `src` import path (`ModuleNotFoundError`);
  repeating with `PYTHONPATH=src` passed both checks without a source change.
- Verification after the annotation/test batch, with
  `PYTHONDEVMODE=1 PYTHONWARNINGS=error`:
  `.aj/bin/python -m pytest -q tests/test_main_defaults.py
  tests/test_gnss_sdr_config_template.py tests/test_gnss_sdr_bridge.py`:
  **186 passed in 2.18 s**. Full `.aj/bin/python -m pytest -q -m 'not usrp'`:
  **418 passed, 1 hardware test deselected in 16.63 s**. The full coverage run
  (`--cov=src/antijamming --cov-report=term-missing`) under the same settings:
  **418 passed, 1 deselected in 22.82 s; 79% aggregate**.
- `.aj/bin/ruff check .`, `.aj/bin/vulture src tests --min-confidence 90`,
  `.aj/bin/python -m compileall -q src tests`,
  separate `bash -n run_realtime.sh` and
  `bash -n tools/run_realtime_sidecar.sh`, and `git diff --check` passed.
  An earlier two-path `bash -n` command checked only its first script; its
  second argument was not a second syntax check. Corrected the command/evidence
  here after running the actual sidecar independently. Verified annotated profile SHA-256:
  `9333ce15dc7009a816610720fbafd410047eb78f1b666b9275b1753a620a6d95`.
- Subsequent live user edit: `//hello` replaced the blank line at profile line 7
  (file mtime 16:15:42+05:00). The repeated focused suite then returned
  **154 failed, 32 passed in 11.23 s**, with JSON consumers rejecting that line
  (`Expecting property name enclosed in double quotes`). The prior passing
  results/hash describe the valid underscore-annotated version, NOT this
  later edited file. Its SHA-256 is now
  `5f4b6d4bf3c2bc27b49fb6d6a14d334165ab92ab5d8c4a783a39d56b4eb191a8`.
  Preserved the user's line while explaining the strict-JSON boundary. Actual
  `//` comments require a coordinated comment-aware reader change, including
  both direct `json.loads` consumers in `run_realtime.sh`, not merely renaming
  the file. The current edited profile is not launchable by these readers;
  comment-format choice is under discussion. No parser change was made.
- No commit/push, branch rewrite, Spark synchronization, RF run, receiver
  rebuild or other-repository change. Main remains `d8aef5f`, experimental
  remains `b60344f`. Existing tar.gz is a previous snapshot, not this annotation
  update. Tests establish the exercised configuration invariants, not absence
  of races, RF correctness, tracking performance or exhaustive comment accuracy.

### 2026-09-07T17:53:22+05:00 — latch/removal confirmation recheck

- Audited cleanup `dfd705a` without changing runtime/configuration or Git
  references. Re-ran the recorded malformed-covariance harness against this
  checkout and the separate unchanged main worktree at `d8aef5f`; the printed
  imported backend paths confirmed that each process used the intended tree.
- Both still release at t=12.1 after valid low evidence at t=10 and malformed
  covariance at t=11. A matched shape-mismatch control (`np.eye(3)` at t=11)
  resets the timer and keeps protection active at t=12.1. Thus the known
  conversion-exception gap remains unresolved; this is not a general latch
  correctness claim. The runnable reproducer is in the existing jammer audit.
- AST-located function-source comparison against pre-removal `6b45b24` found
  exact equality for `_lcmv_jammer_activation_evidence`,
  `_gnss_shared_u1_phase_output_matrix`, `set_lcmv_test_enabled`,
  `_set_lcmv_test_enabled_locked`, and `_activate_lcmv_test_fallback`.
  Git also reports no removal-related changes in GNSS/phase-bank code, radio
  code, IPC/remote-worker implementations, configs, vendored GNSS-SDR or the
  launcher. Unchanged source alone does not prove unchanged integrated behavior:
  the common beam target intentionally changed to measured U1.
- Focused command, with `PYTHONDEVMODE=1 PYTHONWARNINGS=error`:
  `.aj/bin/python -m pytest -q tests/test_beamforming_output.py
  tests/test_shared_u1_phase_compensation.py tests/test_gui_status.py
  tests/test_runtime_evidence.py tests/test_headless_ipc.py
  tests/test_diagnostics.py tests/test_lcmv_summary_script.py`:
  **159 passed in 10.99 s**. It includes measured constraints with a deliberately
  differing valid MUSIC angle, bounded release/reactivation, no-MUSIC FIFO
  recovery, operator-disable solver barrier, phase-bank and GUI/evidence checks.
- Full command under the same warning/development settings:
  `.aj/bin/python -m pytest -q -m 'not usrp'`:
  **415 passed, 1 hardware test deselected in 15.38 s**.
- No new regression was found in these exercised internal paths. Physical
  jammer classification, RF preservation/C/N0/PVT, unknown schedules and
  external TramiqSDR integration remain unverified. Schema-v4 evidence and
  removed ideal/null-angle fields are intentional interface changes; external
  consumers expecting the old fields need separate integration validation.

### 2026-09-07 — ideal-null implementation removed; main policy unresolved

- Scope authorized: remove the entire ideal-null implementation on
  `cleanup/no-usrp-verification-20260831`, starting at `6b45b24`, not merely
  its selector. The ideal-null removal does not affect `main` or the per-PRN
  experimental branch. RF hardware and other repositories are not being changed.
- User approved folding this removal into `4357fcb` and rewriting only its
  cleanup descendants. The local rewrite is complete: `4357fcb` became
  `a7cb0bc`; the replayed tip is `6e1da96`. Recovery refs and the complete map
  are recorded in `audits/commit_history_rewrite_2026-09-04.md`. GitHub's
  cleanup branch was updated from `6b45b24` to `6e1da96` with an explicit
  expected-old-tip lease; `ls-remote` confirmed main and experimental unchanged.
- Added request: audit jammer activation/release in main and cleanup, then
  remove main's added latch/release port. Located main's contiguous port and
  dependent changes: `0a6c600`, `c1cbfb1`, `d8aef5f`; the prior main is
  `5c2a84b`. Returning to that tree restores the historical sticky latch rather
  than making main latch-free. Cleanup retains bounded release; its exercised
  regressions passed after solver migration. The experimental branch is untouched.
- Remove: angle-derived ideal-null solver/export, runtime solve and candidate
  comparison machinery, ideal-method recommendations in current analysis,
  ideal-only tests, and obsolete plot/evidence labels.
- Migrate: common output to the same accepted measured-U1 solution used by the
  protection bank; diagnostics, GUI/headless payloads, and test doubles must
  describe measured-vector operation rather than retain an ideal-mode alias.
- Retain: uniform startup/release/failure output, measured healthy-reference
  capture, measured-U1 constraints, PRN continuity/fanout, and steering models
  used by MUSIC/Bartlett and explicitly calculated response scans. The latter
  are not the removed ideal-null solver or measured OTA suppression.
- Preserve dated audits as historical evidence. Architecture splitting, removal
  of MUSIC activation/guard dependencies, and repair of the separately recorded
  PRN-compensation preservation failure are not implicitly included.
- Baseline verification: `.aj/bin/python -m pytest -q -m 'not usrp'` passed
  **415 tests, 1 deselected in 14.89 s** before implementation edits.
- Main before rollback (`d8aef5f`): focused jammer/release/disable/UI/IPC
  selection passed **14 tests, 117 deselected in 1.05 s**; full non-hardware
  suite passed **303 tests, 1 deselected in 10.13 s**. This does not imply
  physically correct jammer classification or exhaustive timing coverage.
- Intermediate migration runs: **108 passed, 1 failed** (old test expected a
  MUSIC-angle null), then **114 passed, 1 failed** (summary test expected the
  removed candidate-ranking output). Updated the former to check measured-U1
  constraints with intentionally differing valid MUSIC angles; migrated the
  latter to measured-only metrics rather than restore either retired behavior.
- New audit finding, not corrected by ideal-null removal: a covariance
  conversion exception caught before the release-state update leaves the
  previous release timer intact. A runtime harness with low valid evidence at
  t=10, malformed covariance at t=11, and low valid evidence at t=12.1 released
  protection at 12.1 despite the intervening invalid update. The existing
  mismatched-dimension test takes a different path and did not expose this.
  The claim that every invalid update resets the timer is therefore too broad.
  See the amended jammer audit for the reproducible harness and limits.
- The malformed-evidence harness also reproduced the same gap on unmodified
  main `d8aef5f`. No latch correctness claim closes this exception path.
- User clarified that main does not need a latch. Awaiting whether that means
  exact rollback (which restores the older sticky latch) or removal of all
  latch memory with current-evidence-only protection. Main is still unchanged.
- Implemented: ideal-null solver/export and solve dispatch removed; measured
  U1 supplies common/protection targets from one solve; one GUI model curve;
  obsolete null-angle/selected-null-grid fields removed (not empty aliases);
  current summary no longer ranks/recommends retired methods. Steering scans,
  MUSIC guard policy, uniform safety and existing PRN compensation remain.
- Final pre-rewrite code gate: **415 passed, 1 hardware test deselected in
  23.33 s**, with `PYTHONDEVMODE=1 PYTHONWARNINGS=error`, coverage enabled,
  **79%** aggregate. Before the final explicit absence regression, the focused
  DSP/runtime/evidence/UI/summary/IPC/phase-bank set passed **158 in 11.93 s**.
  Ruff, Vulture >=90%, compileall, shell syntax, and diff checks passed.
- Negative controls: the new solver/export absence regression and the
  intentionally wrong-MUSIC-angle runtime regression both failed as expected
  when executed against a detached, unchanged `6b45b24` source tree.
- Source/tools/config search found no ideal-null solver/method, ideal-LCMV
  metrics, or selected-null fields. Remaining test references assert absence;
  dated audits retain historical evidence. This is targeted contract review,
  not exhaustive examination of all repository code or thread schedules.
- Post-rewrite verification at `6e1da96`: the complete source, tests, tools,
  profiles, vendored receiver and launcher match verified snapshot `78924a3`
  byte-for-byte (`git diff --exit-code 78924a3 -- src tests tools configs
  gnss-sdr run_realtime.sh`). The complete warnings-as-errors/development-mode
  coverage suite passed **415 tests, 1 deselected in 23.34 s; 79% aggregate**.
  Ruff, Vulture >=90%, compileall, shell syntax and diff checks passed again.
- Intermediate rewritten cleanup `a7cb0bc` passed **397 tests, 1 deselected
  in 17.22 s** with warnings-as-errors/development mode. The later release
  replay migrated its solver-barrier and GUI tests to the measured contract;
  its focused gate passed **14 tests, 69 deselected in 1.34 s**. Later release,
  FIFO and UHD implementations were not transplanted into the earlier commit.
- Retained with active consumers: measured-candidate diagnostics feed current
  summaries/status; model-coherence diagnostics compare MUSIC steering with
  measured U1; uniform/fallback and optional startup state handle real lifecycle
  paths. These are not empty ideal-mode aliases. Historical audits and local
  recovery refs remain evidence, not runtime product features.
- Publication initially stopped because no HTTPS credential helper was
  configured. Retried using the user's previously authorized desktop token
  through a temporary askpass helper, without storing credentials in Git or
  project files. The rewrite push succeeded. This evidence is a subsequent
  documentation-only commit; default-config/documentation tests passed
  **67 tests in 0.20 s** after the ledger/map edits.
- Pending: main policy decision. Spark and other repositories remain
  untouched; no RF bins or hardware settings changed. The local disposable
  negative-control worktree can be removed after checks; preserved Git refs
  retain the old source and the verified implementation snapshot.

### 2026-09-04 — classify every project-branch commit

- Rewrote commit subjects on `main`, `per-prn-fifo-experimental`, and the
  cleanup branch so every subject starts with a Conventional Commit type.
- Verified that each branch's rewrite-checkpoint tree ID is byte-identical
  before and after the metadata-only rewrite. Historical run documents retain
  their original commit IDs; the old-to-new provenance map is in
  `docs/audits/commit_history_rewrite_2026-09-04.md`.
- Added the durable commit-type rule to `/home/u/AGENTS.md` for repositories
  owned by `UsmanHassan-0`.

### 2026-09-02T19:43:47+05:00 — bounded jammer-protection release

- Cleanup commit `86eca715c18b18dff308588fcc20c9e15e3e5617` separates
  historical jammer detection from current protection. It uses 3/6 dB
  activation thresholds, lower 1.5/3 dB release thresholds, and a two-second
  release hold. Release returns both common and GNSS FIFO output toward
  uniform even if the MUSIC target disappears. The hold and thresholds are
  implementation provenance, not OTA-tuned values.
- A re-entrant control lock serializes operator enable/disable with complete
  LCMV worker publication. A deterministic barrier test verifies that an old
  in-flight worker cannot republish active state after disable returns.
- Focused verification passed 150 tests. Full, warnings-as-errors, and coverage
  runs each passed 412 tests with one opt-in USRP test skipped; aggregate
  coverage was 79%. Ruff, Vulture, `compileall`, shell syntax, and diff checks
  passed. These results prove only their exercised software paths and do not
  prove a physical jammer ON-to-OFF cycle.
- Detailed implementation boundaries are recorded in
  `docs/audits/jammer_latch_audit_2026-09-01.md`.

### 2026-09-01T18:15:24+05:00 — jammer-latch release audit

- Confirmed that the cleanup product has a historical jammer-detection latch,
  not automatic current-protection release. When current power-plus-covariance
  evidence disappears, `evidence_now` becomes false but the latch stays true
  until LCMV is disabled.
- A current-code harness showed that an outside MUSIC peak keeps common LCMV
  active after evidence disappears. With no outside peak, common status falls
  back to uniform but the GNSS FIFO retains the last measured-U1 protection
  while the latch remains true. Manual disable clears both.
- Located the remembered release implementation only on
  `origin/per-prn-fifo-experimental`: commit `9a23c86` separates historical
  detection from `lcmv_jammer_protection_active` and releases after a two-second
  hold. That value and implementation are not current-product or OTA-validated,
  and the large experimental branch must not be merged wholesale.
- Reproduced an operator-disable race with a deterministic solver barrier. OFF
  first produced disabled/uniform/no-protection state, but the in-flight worker
  later published ON status, non-uniform target weights, and GNSS protection
  availability while runtime/config enable flags remained false.
- The GUI's generic “Uniform fallback” wording can disagree with the actual
  retained GNSS FIFO protection row. Detailed handoff labels carry the actual
  source, but the primary status does not expose current protection separately.
- Current focused tests passed `3 passed in 0.14s`; their exact boundary and the
  older non-proof OTA evidence are recorded in
  `docs/audits/jammer_latch_audit_2026-09-01.md`. No runtime code changed and no
  current hardware release claim is made.

### 2026-09-01T16:15:04+05:00 — 4–10 MS/s sweep opened

- Recorded the actual starting boundary before further runs: 4 MS/s has
  attached RF/tracking/PVT evidence; 5 MS/s has a 1,200-second transport-only
  soak; 5 MS/s RF/PVT and all attached 6–10 MS/s rates remain pending.
- The one untouched-`main` FIFO-source-9 stall remains an open root-cause
  investigation. The fair striped writer and 0.250-second deadline exist in
  both revisions, so the longer cleanup success is not by itself evidence that
  cleanup fixed the stall.
- The rate sweep will use temporary profiles and the retained 1,200-second
  waveform, leaving the checked-in 4 MS/s product profile unchanged. Results
  and failures are added immediately to
  `docs/audits/sample_rate_sweep_2026-09-01.md`.
- Added and ran a seven-case 4–10 MS/s propagation regression plus the existing
  32-channel/5 MS/s renderer case: `8 passed`. It proves config agreement from
  the single authored rate through X300 followers and every rendered GNSS-SDR
  source/conditioner; it does not prove attached realtime capacity.
- The attached 5–10 MS/s RF sweep was paused before its first new run after the
  bladeRF was physically removed. Spark still detected the X300, but neither
  USB enumeration nor `bladeRF-cli` found a bladeRF. No temporary rate profile
  or runtime was started, so RF/tracking/PVT evidence above 4 MS/s remains
  pending (the earlier 5 MS/s result remains transport-only).
- Full warnings-as-errors/development-mode verification after the new
  parameterized rate contract passed: `407 passed, 1 skipped in 15.06s`; the
  skip remains the explicitly opt-in physical-USRP pytest.
- Configured Ruff, Vulture at 90% confidence, Python `compileall`, product
  shell syntax checks, and `git diff --check` passed for this checkpoint.

### 2026-09-01T15:36:46+05:00 — bladeRF gain, `main` control, and direct-file checkpoint

- Confirmed the reconnected splitter path with the new 1,200-second,
  50 MS/s L1 waveform. The cleanup branch ran 980.233 seconds with zero FIFO
  drops, an empty error log, and repeated valid GNSS PVT. Steady bladeRF 10 dB
  produced eight PRNs averaging about 43.25–43.41 dB-Hz; 15 dB was about
  46.61–46.89 dB-Hz and 20 dB about 48.93–49.28 dB-Hz. Ten dB is the preferred
  level for this exact test chain, not a calibrated RF-output claim.
- Ran an isolated untouched-`main` control at commit `0672377` with the same
  GNSS-SDR binary, 4 MS/s profile, file restart, and bladeRF 10 dB. It initially
  tracked the same eight PRNs and produced valid PVT, then reproduced a
  16,384-byte stall on zero-based FIFO source 9 at the 0.250-second timeout.
  Its summary recorded one drop. The longer cleanup run did not reproduce that
  failure; this comparison remains bounded to the two observed schedules.
- A separate 1,200-second 5 MS/s attached-X300 soak completed with 182,405 raw
  chunks, zero overflow/timeout/clipping indicator, and 182,404 ten-source FIFO
  writes with zero drops. This does not establish 32-channel realtime capacity.
- Ran GNSS-SDR directly on a 120-second prefix with RF, USRP, anti-jamming, and
  FIFOs absent. It decoded all eight intended PRNs, emitted 86 valid PVT epochs
  and 171 positions, reached receiver time 120 seconds, and exited normally in
  62.035 seconds. This establishes the tested waveform prefix independently of
  the RF chain, not all 1,200 seconds or every ephemeris cutover.
- Exact commands, paths, counts, hashes, failure text, and claim boundaries are
  retained in
  `docs/audits/gnss_pipeline_main_control_2026-09-01.md`.
- Final laptop gates after the evidence/documentation batch: focused GNSS
  bridge and documentation set `172 passed`; full development/warnings-as-error
  suite `400 passed, 1 skipped`; coverage suite `400 passed, 1 skipped` at 78%
  aggregate project-source line coverage. The skip remains the opt-in physical
  USRP pytest; attached Spark runs are recorded separately rather than hiding
  that gate. Configured Ruff, Vulture at 90% confidence, `compileall`, product
  shell syntax, and `git diff --check` all passed.
- After fast-forwarding Spark to source/evidence commit `06df103`, its full
  development/warnings-as-error suite also passed: `400 passed, 1 skipped` in
  10.53 seconds. The primary Spark and laptop worktrees were clean at the same
  commit before this documentation-only record was added.
- Documentation correction made during follow-up review: the RF run started
  LCMV-off but auto-armed after healthy PVT at session elapsed 540.123 seconds.
  It remained on uniform fallback because neither jammer-evidence gate fired,
  so no covariance-null weights were applied. The earlier shorthand “LCMV was
  disabled” was inaccurate even though the run was not a nulling test.

### 2026-08-31T22:57:13+05:00 — Spark attached-hardware checkpoint

- Transferred exact commit `061e12f` to Spark by Git bundle without a GitHub
  push. Both cleanup branches matched, both working trees were clean, and both
  `main` references remained at `0672377`.
- Spark's ARM environment passed `397` software tests with the one physical
  USRP gate skipped; the explicitly enabled four-channel USRP smoke test then
  passed separately.
- Corrected Spark's live socket-buffer sysctls from 33,554,432 to the
  repository-specified 50,000,000 bytes after UHD reported the smaller value.
  The repeated bounded run had zero UHD buffer warnings, 3,063 receive chunks,
  no overflow/timeout/clipping indicator, ten synchronized GNSS FIFOs with no
  drops, empty `errors.log`, and no remaining owned process after shutdown.
- The short run had no PVT, no LCMV activation, and no controlled jammer. Its
  automatic audit correctly kept jammer state unknown and suppression
  unavailable.
- The bounded bladeRF screen used X300 gain 0 dB and bladeRF gains −23, 0, and
  +10 dB. All TX-on power readings remained within 0.2 dB of the matched TX-off
  capture despite a nonzero IQ window. TX returned idle after each test. No
  higher gain was attempted: the bladeRF-to-four-input physical path remains
  unproven and requires cable/splitter/port confirmation.
- Exact run paths, hashes, counters, profile observations, and claim limits are
  retained in `docs/audits/cleanup_spark_hardware_2026-08-31.md`.

### 2026-08-31T22:26:31+05:00 — strict active-contract software checkpoint

- Removed the optional preserve/target selectors, configuration-disabled
  single-FIFO path, RF-budget code, experiment overlay, old GNSS bridge facade,
  obsolete executable/state aliases, and their test-only compatibility shapes.
  Uniform output remains because it is the active safety state; healthy-U1
  capture remains because the fixed measured-U1 constraint requires it. The
  strongest eligible MUSIC peak outside the frozen bladeRF guard remains the
  fixed ordinary target-selection algorithm, not a selectable legacy mode and
  not physical jammer truth.
- Normalized GNSS state through the active producer/IPC/UI contract. Internal
  maps use `(constellation, PRN)` keys; every public record carries
  `constellation`, `prn`, and `satellite_id`; public summary lists use labels.
  GPS-only integer list aliases and consumer fallbacks are absent. A GPS/BeiDou
  equal-PRN regression proves collision separation for that exercised case.
- Traced the typed USRP receive result through the backend, hardware smoke test,
  standalone source-count tool, and fakes. This found and removed a stale tuple
  unpack in `tools/usrp_source_count_snapshot.py`. Constant-time validation now
  rejects wrong result type, channel count, rank, dtype, or sample count before
  IQ consumption. Unsupported UHD metadata is retried once at startup and is a
  runtime failure later; its IQ is not published to DSP or GNSS queues.
- Added deterministic regressions for the Shared-U1 submit/stop sentinel race,
  retained handoff dependencies after a missed join, GNSS NMEA PTY startup
  ordering, successful-protocol/failed-start IPC replies, verified per-channel
  LO-lock sensors, and invalid UHD packet/sample metadata. Each result is
  bounded to the injected schedule or fake interface.
- Made diagnostic-sidecar teardown one-shot and process-group owned. Its
  focused SIGTERM harness verifies one final manifest transition and reaping of
  every launched monitor group; other signal schedules remain unproven.
- The GUI and schema-version-3 full-angle record now separate the ordinary
  common ideal-target response from the shared measured-U1 FIFO response. Both
  are labeled computed ideal-steering scans, never measured OTA null depth. The
  ordinary marker belongs to the strongest eligible MUSIC target only. The
  active status calls this an interference-evidence gate, and the stale
  `confirmed_jammer_bearing` claim is absent because no ground-truth jammer
  bearing enters this path.
- Full development-mode suite after these changes:
  `397 passed, 1 skipped` with `PYTHONFAULTHANDLER=1`,
  `PYTHONMALLOC=debug`, `PYTHONDEVMODE=1`, and `PYTHONWARNINGS=error`. The skip
  is the explicit physical-USRP smoke test. A separate full coverage run also
  passed at 78% aggregate project-source line coverage. Configured Ruff,
  `compileall`, shell syntax, `git diff --check`, and Vulture review also ran;
  the 60% Vulture reports were generated protobuf descriptor fields, UHD
  command attributes, and a pytest-discovered fixture. Attached behavior and
  unknown concurrency schedules remain unproven.

### 2026-08-31T18:07:13+05:00 — second completion claim retracted

- The 17:16 software gates remain valid for commit `9265a3c`, but they did not
  justify describing the whole semantic cleanup as complete.
- `uniform`/`healthy_reference` preserve modes, the
  `strongest_music_peak` target mode, and configuration-disabled single-FIFO
  operation remain reachable without a demonstrated current product consumer.
- Essential uniform safety output and healthy-reference capture are separate
  from those optional runtime modes and must not be removed with them.
- The offline RF calculator and historical RF/expected-bearing summary parsing
  also remain even though the product runtime no longer produces their inputs.
- Status: semantic review is active again. The next pass must classify these
  paths by current product intent and rerun evidence after any removal.

### 2026-08-31T17:16:54+05:00 — semantic cleanup software verification

- Full hardware-free suite: `333 passed, 1 skipped`; the skip remains the
  explicit `RUN_USRP_TESTS=1` hardware smoke test.
- Coverage-instrumented full suite: `333 passed, 1 skipped`, 77% aggregate
  project-source statement coverage.
- Warning-as-error/development-mode full suite: `333 passed, 1 skipped`.
- Configured Ruff, Vulture at 90% confidence, `compileall`, both product shell
  syntax checks, and `git diff --check` passed.
- These gates verify only the exercised software paths and schedules. They do
  not establish real GNSS-SDR tracking, X300/TwinRX resource timing, RF
  correctness, OTA null depth, or absence of all races.

### 2026-08-31T17:13:32+05:00 — semantic stale-interface removal batch

- Removed the expected-bearing/RF-bench experiment configuration and runtime
  calculations. Historical measured values remain in dated audit evidence; the
  runtime no longer receives authored bladeRF/jammer angles or distances.
- Removed the runtime-overlay loader, environment variable, checked-in overlay,
  sidecar merge, rejection compatibility branch, and overlay-only tests. The
  product has one checked-in runtime profile.
- Removed the inactive in-process Qt `StreamWorker`, the temporary GNSS bridge
  facade, legacy GNSS executable/path/state aliases, unread runtime/UI caches,
  and test-only UI/theme constants. Current GUI execution remains
  `RemoteStreamWorker` -> headless service -> `BackendRuntime`.
- Removed the unread `auto_rate_backoff` and `cep_window_points` configuration
  fields and normalized internal satellite keys to `(constellation, PRN)` while
  retaining the GPS-compatible public snapshot shape.
- The focused configuration, beamforming, GUI, and theme suite passed: `137
  passed`. Ruff, `compileall`, shell syntax, and `git diff --check` passed for
  this intermediate batch. Broad gates remain pending and no attached-hardware
  claim is made.
- Low-confidence dead-code review classified the remaining reports: protobuf
  descriptor attributes are generated; UHD `stream_now` is an external command
  property; GNSS PTY and backend thread attributes are reached through shared
  mixin/dynamic ownership helpers; the pytest fixture is plugin-discovered.

### 2026-08-31T16:17:17+05:00 — semantic cleanup completion claim retracted

- The earlier checkpoint established the named lifecycle, input, numerical, and
  static-analysis invariants, but it did not establish that every reachable
  configuration mode still belonged in the intended product.
- Follow-up inspection found that the manually authored expected-bearing path
  remains callable through `expected_jammer_range_peak_or_center`, even though
  the checked-in product profile uses live measured preservation and no current
  profile supplies expected bearing ranges.
- The optional bladeRF/jammer experiment overlay is not part of a normal launch.
  When explicitly selected, its checked-in fields contribute experiment logs,
  RF-budget estimates, and calibration-context warnings rather than controlling
  the live transmitters or the current automatic DoA/LCMV target selection.
- High-confidence Vulture and configured Ruff did not expose this issue because
  reachable branches, compatibility exports, and test-referenced behavior are
  not syntactically dead. Test reference is evidence of reachability, not
  evidence of current product intent.
- Status correction: the lifecycle/input/numerical batch remains verified under
  its recorded conditions, but the broader stale-code cleanup is active again.
  No hardware-free cleanup completion claim is currently in force.

### 2026-08-31T15:25:57+05:00 — lifecycle/input batch verification checkpoint

- Full suite passed twice after the last material code batch: `345 passed, 1
  skipped`. One run enabled `PYTHONFAULTHANDLER=1`, `PYTHONMALLOC=debug`,
  `PYTHONDEVMODE=1`, and `PYTHONWARNINGS=error`.
- Coverage-instrumented full suite passed three consecutive times at `345
  passed, 1 skipped`; the reported aggregate source coverage was 77%.
- The skip was explicit: `RUN_USRP_TESTS=1` is required for the exclusive USRP
  hardware smoke test. No attached-hardware conclusion replaces that skip.
- Selected lifecycle failure schedules passed ten consecutive repetitions, 14
  focused tests per run.
- Configured project Ruff, high-confidence Vulture, `compileall`, shell syntax,
  changed-module isolated mypy, and `git diff --check` passed.
- Extended non-gate Ruff diagnostics remain classified rather than hidden: 70
  style/complexity findings (mostly suppressible-exception and redundant-cast
  suggestions) and 41 security diagnostics (mostly best-effort teardown catches
  and subprocess review prompts). No parser/error-class or async-rule finding
  appeared. See “Deliberately unchanged or deferred” for boundaries.
- Documentation existence/numbering tests enforce consecutive conceptual files
  `00`–`08` plus the single tracker, provenance map, and audit index.
- Historical status at this checkpoint was recorded as hardware-free branch
  completion. The 16:17 amendment above retracts that broad interpretation;
  only the named lifecycle/input/numerical batch remains complete under its
  recorded conditions. Physical X300/TwinRX/UHD, GNSS-SDR receiver, RF,
  antenna, and OTA verification also remains outstanding.

### 2026-08-31T14:52:48+05:00 — documentation consolidation

- Replaced multiple living cleanup/progress documents with this one tracker.
- Kept run-specific records as evidence under `docs/audits/`; a historical
  audit is not treated as current implementation truth.
- Separated conceptual numbered guides from dated evidence and added a stable
  implementation-provenance map.
- Removed the stale source inventory only after confirming that its proposed
  `gnss/gnss_sdr.py` split had already happened and its `runtime/worker.py`
  path no longer described the product runtime.
- Status: documentation mutation in progress; final reference and test gates
  have not yet run.

### 2026-08-31 — hardware-free lifecycle and hygiene cleanup

The following statements are deliberately bounded. “Regression passed” means
the injected schedule was reproduced and exercised; it does not exclude all
possible thread schedules.

| Component | Previous behavior observed | Current cleanup-branch behavior | Evidence status |
| --- | --- | --- | --- |
| TwinRX LO polling | Sleep was unreachable after the polling loop, allowing timeout busy-polling | Sleep occurs between fake sensor polls | Focused fake-clock transition/timeout regressions passed; hardware untested |
| IPC accept/close | Final accepted session could be absent from an early session snapshot | Acceptor is stopped before the final session shutdown pass | Local Unix-socket multi-client and injected partial-start regressions passed |
| Shared-U1 sample ownership | Monitor queue could retain a view of producer-owned mutable IQ | Submission owns a contiguous copy; stop retains live thread/handle on timeout | Deterministic blocked-worker and mutation regression passed |
| UHD event monitor | Stop could discard a live thread then mutate shared scanner state | Timed-out owner is retained; shared finalization follows worker exit | Deterministic blocked-worker regression passed; UHD hardware untested |
| IPC client | Close/connect publication and reader ownership were not synchronized | State publication is locked; reader is joined/retained according to liveness | Barrier-controlled late-connect and reader-shutdown regressions passed |
| IPC server/session | Thread-start failures could leave partial session/server resources | Startup is transactional and rollback closes owned resources | Injected accept/sender start failures and FD checks passed |
| GNSS-SDR bridge | PTY/FIFO/log/monitor resources could survive startup exceptions | Startup rolls back all resources acquired before failure | Injected `Popen` failure with FD/path/owner assertions passed; real receiver untested |
| Backend | Device stop followed RX join and timed-out thread refs could be discarded | Device is stopped before RX join; live timed-out refs remain owned | Blocking fake-device order, join-timeout, stage-failure, and concurrent-start regressions passed |
| Remote worker | Immediate start reply could arrive before request ID publication; rejected start could remain requested | Request ID is installed before send; negative/failed starts release state | Deterministic immediate reply/failure regressions passed |
| GUI subprocess | Partial GUI startup could leave marker/service child; normal exit did not guarantee escalation/reap | Idempotent cleanup performs wait, TERM, KILL, and final reap | Fake child escalation/reaping regression passed |
| Logging | Reconfiguration detached file handlers without closing them | Replaced handlers are flushed and closed | Warning-as-error suite exposed the leak; focused and full suite then passed |
| Qt teardown | Coverage run reproduced deferred graphics-item destruction abort | Timers are widget-owned and deferred deletes are drained in fixture | Five earlier repeated coverage runs passed; other Qt/platform schedules unproven |
| Runtime profile | Unread accuracy-log interval and bench-only RF metadata appeared as product controls | Unread field removed; bench metadata, expected bearing ranges, and the checked-in experiment overlay are absent from live configuration; historical values remain in audits | Schema/search/config tests passed; focused semantic-removal regressions passed; broad gates pending |
| Runtime JSON | Duplicate keys and non-standard `NaN`/`Infinity` could be accepted; `NaN` bypassed the positive-rate comparison | Duplicate and non-finite values fail at the parse/load boundary | Duplicate/non-finite/string-rate regressions passed |
| Removed runtime overlay | Fields were mutated in iteration order before a later invalid key failed | The loader, environment-variable branch, checked-in experiment overlay, and rejection compatibility path are removed; launchers use one fixed product profile | Fixed-profile regressions passed; broad gates pending |
| DSP entry contracts | Empty/non-finite data could yield warnings, NaN payloads, or a meaningless first-grid DoA | Phase/DoA/steering/combiner/diagnostic paths reject named invalid shapes and non-finite inputs | Focused shape/empty/non-finite regressions passed |
| LCMV covariance contract | Solver guarded conditioning/weight norm but did not reject non-Hermitian/non-PSD covariance or non-finite control limits explicitly | Covariance and control parameters now have explicit mathematical-domain checks | Invalid-domain tests plus 100 deterministic random PSD constraint cases passed |

Earlier verification checkpoints retained from the in-progress batches:

- `PYTHONDEVMODE=1 PYTHONWARNINGS=error`: `313 passed, 1 skipped`.
- Focused bridge suite: `84 passed`.
- Focused IPC injected failures: `9 passed`.
- Focused config/DSP/lifecycle set after the input batch: `94 passed`.
- Ten consecutive selected lifecycle stress repetitions: each run `14 passed,
  142 deselected`.
- Changed-module mypy run with imports skipped/ignored: clean for the eight named
  modules. Whole dependency-following mypy remains unclean: `477 errors in 24
  files`, dominated by generated protobuf, dynamic mixin, untyped UHD/Qt, and
  backend dictionary typing debt.
- Configured project Ruff gate: clean after excluding the vendored GNSS-SDR
  subtree from project-owned lint.
- These earlier counts are superseded by the final checkpoint above, but remain
  recorded to show when each correction batch was exercised.

Known diagnostic debt, not represented as clean:

- Whole-package mypy has a substantial existing backlog involving generated
  protobuf modules, dynamic mixins, Qt stubs, untyped dependencies, and backend
  dictionary types. This cleanup does not claim type-checker cleanliness.
- Extended Ruff security/style diagnostics include deliberate teardown catches,
  controlled list-form subprocess launches, a predictable `/tmp` socket with
  permission/type defenses, and style findings. Each needs classification;
  their existence alone is not proof of a vulnerability or behavior defect.

## Deliberately unchanged or deferred

- `runtime/backend.py` and `ui/main_window.py` remain large. Splitting them is a
  separate architecture task because moving ownership boundaries during this
  lifecycle cleanup would make behavior comparison harder.
- Uniform output and healthy-reference capture remain active algorithm
  necessities, not selectable preserve modes. The ordinary target selector is
  fixed to the strongest eligible MUSIC peak outside the frozen bladeRF guard;
  it is not a physical-jammer classifier and has no alternate runtime selector.
- Uniform output while measured-U1 activation evidence is unavailable remains
  an active safety state, not a stale compatibility path. It prevents applying
  unproven null weights until the live angle cluster, healthy receiver state,
  frozen U1/covariance, freshness, and jammer-evidence gates are satisfied.
- The per-user/PID Unix sockets remain under sticky `/tmp` with socket-type
  refusal and mode `0600`. Static security lint flags predictability; changing
  the operational path needs a compatibility/threat-model decision. No command
  injection was established in list-form controlled subprocess launches.
- Best-effort exception handling in low-level UHD teardown was not mass-rewritten
  without hardware evidence. Silent signal-handler registration failures were
  changed to logged warnings because that path was independently observable.
- The customized vendored `gnss-sdr/` tree is excluded from project-owned Ruff
  lint. It is not claimed clean; it has its own upstream/build/test boundary.
- `main` and `per-prn-fifo-experimental` remain unchanged.

## Review and reading scope

- Completely read for this task: applicable `AGENTS.md` files, the reusable
  review playbook, the active tracker, all conceptual numbered docs, selected
  current architecture/hardware/audit docs, and the full MUSIC, LCMV,
  calibration, diagnostic, and DSP-stage source files.
- Read targeted ownership sections to EOF-relevant boundaries for backend,
  headless/GUI startup, IPC, Shared-U1, GNSS bridge monitors, logging, and UHD
  event monitoring; combined these with call-site searches, coverage, static
  tools, and deterministic regressions.
- Performed repository-wide searches for thread starts/joins, subprocesses,
  sockets/files/FD acquisition, broad exception handlers, stale doc references,
  and high-confidence dead code.
- This was not a manual reading of every line, word, or symbol in the 7,000+
  line backend, UI implementation, vendored GNSS-SDR, or every historical audit.
  Coverage/static/search results are not represented as exhaustive manual
  review.

### Pre-2026-08-31 RF/DSP implementation snapshot — source date absent

This table preserves the useful content of the former progress document. Its
original source did not record a timestamp or exact artifact for every “yes,”
so these are historical project-status notes, not newly established proof.

| Capability | Implemented snapshot | Historical RF status / limitation |
| --- | --- | --- |
| USRP stream to GNSS-SDR and baseline PVT | Yes | Partial live evidence; repeat with attached X300 |
| Phase and complex-gain calibration | Yes | Conducted calibration, not a complete OTA manifold calibration |
| Steering/angle convention | Yes | Physical orientation still needs controlled RF proof |
| MUSIC and Bartlett diagnostics | Yes | Partial live evidence; disagreement requires controlled angle sweep |
| Covariance LCMV and measured-U1 candidate paths | Yes | Candidate behavior depends on preserve/null model and labeled live data |
| Desired-loss/noise-gain/spatial-vector diagnostics | Yes | Healthy reference can be absent and dominant U1 is not always a jammer |
| Shared measured-U1 fanout with per-PRN continuity scalar | Yes | One shared spatial solution, not an independent per-PRN LCMV solve |
| GSC exploration | No; proposed | Design only after the present covariance LCMV path has authoritative live evidence |
| Automated jammer confidence/gating | Partial | Thresholds require labeled hardware tuning |

Detailed historical evidence is indexed in `docs/audits/README.md`.

## Open hardware and research questions

- How stable is the OTA manifold compared with conducted splitter calibration?
- Which candidate improves PVT/C/N0 under a real jammer without harming the
  jammer-off baseline?
- What receiver-health thresholds distinguish healthy baseline from a
  jammer-like event?
- Does the frozen measured bladeRF U1 preserve every tracked PRN through a
  sustained jammer interval, or is a desired multi-vector subspace required?
- Tune white-noise-gain, predicted suppression, input-power, and generalized
  covariance gates only from labeled retained runs; angle motion alone is not
  physical jammer truth.
- GSC remains proposed, not implemented.

## Required verification gates

After each material batch, run focused regressions followed by the applicable
repository gates:

```bash
.aj/bin/python -m pytest -q
.aj/bin/python -m pytest --cov=src/antijamming --cov-report=term-missing -q
.aj/bin/ruff check .
.aj/bin/vulture src tests --min-confidence 90
.aj/bin/python -m compileall -q src tests
PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q
git diff --check
```

Record counts, repetition counts, failures, and skipped/deselected hardware
tests here. Do not turn the gate list itself into a claim that it ran.

## Provenance vocabulary

- **Implemented:** present in the named code path.
- **Standard-defined:** required or described by a named external standard.
- **Paper-derived:** adapted from a named publication, with deviations stated.
- **Measured:** retained run/log with configuration and hardware context.
- **Derived:** calculated from stated inputs or equations.
- **Assumed:** used without direct measurement.
- **Proposed:** not implemented or validated.

Do not collapse these categories into “works” or “fixed.”
