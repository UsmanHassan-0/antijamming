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

## Current dashboard

| Area | Current state | Next evidence gate |
| --- | --- | --- |
| Proven stale code/config | Ideal-null solver, exports, rankings, duplicate plot and retired fields removed coherently; targeted software gates passed | No claim that every repository path is free of stale behavior |
| Thread/resource ownership | Deterministic failure paths and repeated software gates passed | Unknown schedules and hardware timing remain unproven |
| Concurrent transitions | Selected start/stop/connect/publish schedules regression-tested | Repeated stress; unknown schedules remain unproven |
| Configuration/input boundaries | Focused and broad regressions passed | Attached-runtime validation |
| Numerical/DSP boundaries | Focused and broad deterministic regressions passed | OTA correctness remains separate |
| Jammer release audit | Malformed covariance can bridge the release hold in current main and cleanup; reproduced, not corrected | Main rollback policy needs clarification; exception-path correction remains open |
| Documentation | One tracker, consecutive conceptual docs, retained audit index, and provenance map | Maintain these documents with future changes |
| Hardware integration | Previous archived revisions have bounded RF/transport evidence; no September 7 hardware run or Spark synchronization | Validate the new measured-only common target and final FIFO behavior separately |

## Timestamped change log

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

### 2026-09-04 — source-built UHD 4.10 runtime selection

- Baseline failure: distribution UHD 4.6 rejected X300 serial `35D068E` with
  FPGA `39.3` because it expected an older radio compatibility revision.
- Built official UHD tag `v4.10.0.0`, commit
  `2af4ddb96219a99d2300804830e0971f79557b23`, with PyUHD under the user-local
  prefix `~/uhd-official-4.10.0.0/install-host` and build directory
  `~/uhd-official-4.10.0.0/build-host`. No system library was replaced. The
  source checkout is detached at the pinned commit; its only reported untracked
  path is the in-tree `install-host/` build output.
- Spark artifact SHA-256 values after the verified build:
  `libuhd.so.4.10.0` =
  `e8c4fdfcfce3a23672db14a2dcdd0bedc49d722743f73806701038545107595e`,
  `libpyuhd.cpython-312-aarch64-linux-gnu.so` =
  `86fcab4f285e8bda4366c24bf114bc2dd9583b74a1b06d93ba6f4535ba8d705e`,
  and `uhd_usrp_probe` =
  `8b104a59fd4453e873084454d50c5e81a068d30ccc4a086fdeb61745b1ad2b35`.
- The unchanged anti-jamming backend opened the X300 through source-built
  PyUHD and completed a 30-second no-jammer runtime: 3,110 raw chunks, zero
  UHD overflows, zero timeouts, and normal GNSS-SDR startup/shutdown.
- Added a fail-closed launcher boundary that validates UHD version, module
  location, and native binding location before GUI/headless startup.
- A second 20-second no-jammer run used only the checked-in launcher selection
  and processed 1,878 raw chunks with zero overflows and zero timeouts.
- The repo-local GNSS-SDR executable remains linked to GNU Radio/UHD 4.6 and
  uses FIFO sources in a separate process. Full-stack migration requires a
  matched GNU Radio and GNSS-SDR rebuild and is not claimed by this change.
- Branch scope was verified after the port: UHD 4.10 exists only on
  `cleanup/no-usrp-verification-20260831`. Remote and local `main` were restored
  to `d8aef5ff2d9ca6e335a355d1d98e07b80aa12300`; its launcher and tree contain
  no UHD 4.10 activation scripts or references. The temporary build branch was
  deleted.
- Post-port laptop verification on the cleanup branch passed 70 focused
  runtime/default tests and the complete non-hardware suite passed 415 tests
  with one hardware test deselected. Re-running the same jammer release,
  no-target uniform recovery, operator-disable race, GUI, headless IPC, and
  per-PRN jammer-off continuity regressions against `main` passed; `main`'s
  complete suite passed 303 tests with one hardware test skipped. These are
  deterministic software results, not a physical jammer ON-to-OFF trial.

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
