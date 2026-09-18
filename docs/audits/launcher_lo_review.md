## Scope and source identity

2026-09-17, laptop `cleanup/no-usrp-verification-20260831`, HEAD
`dfd705afade90f8419c73fff210b17947f433d15`, plus the preexisting uncommitted
JSONC/automatic-control/CEP work. The user requested correction of the launcher
typo and LO-test assumption, plus review of the attached independent assessment.
This is not permission to alter RF calibration, jammer logic or other branches.

The new attachment and the earlier September 15 report have the same SHA-256:
`fed11df145cec08e2c361d6cd5acc01719faf2d8442b1a8a52a3be0ea0dfa8e4`.
The full attachment was read. Its linked `sandbox:/mnt/data` bundles were not
provided as local files; their additional contents were not independently read.

Raw evidence is in
[`audit-results/launcher_lo_review_gce0zJ/`](../../audit-results/launcher_lo_review_gce0zJ/).
It includes baseline file copies, Git refs/status/diff, affected-file hashes,
JUnit reports, command output, and a software-only probe. The complete shared
instructions, review playbook and living tracker were read; source inspection
was targeted, not a claim of exhaustive semantic review of the repository.

### Launcher

The working launcher imported `re` but called `rey.search`. Its real embedded
Python failed before returning the USRP address. The wrapper had already
started the GUI when it attempted this lookup. A disposable-copy harness using
the actual shell and actual JSONC reader reproduced both failures, with stub
GUI/UHD/route/process/sidecar boundaries and no hardware or real GUI launch.

Corrections in `run_realtime.sh`:

- Call `re.search`.
- Resolve the diagnostic network interface before creating the GUI process.
- Assign the Python helper output separately, so its failure status propagates
  instead of being nested inside the route helper's arguments.
- Preserve interface override, logging-disabled, missing-sidecar, original GUI
  arguments and application exit-status behavior.

The new `tests/test_realtime_launcher.py` exercises successful route discovery,
GUI statuses 0 and 7, missing-address failure, injected Python lookup exception,
explicit interface override, logging disabled and absent sidecar. The failure
cases require that neither GUI nor sidecar starts. The stubs exit immediately;
the earlier failure cannot leave a persistent test child. The existing
`tests/test_jsonc.py` also executes both actual shell readers without Python site
packages, covering the parser boundary independently.

This does not establish all launcher lifecycle schedules. For example, no
physical UHD activation, real GUI/receiver process tree, network reconfiguration
or signal arriving during later process startup was tested. Existing behavior
when a route command returns no interface, and sidecar monitoring internals,
were not redesigned in this narrowly scoped correction.

### LO test, not a new RF calibration

The user's intended sources remain `[internal, companion, reimport, reimport]`
and exports remain `[true, false, false, false]`. Only Board A's first channel
exports. Neither the runtime profile nor the device implementation was changed.

The default-map regression had still asserted `[true, false, true, false]`.
Its expected value and name now express the intended A-master configuration.
Additional tests trace `StreamConfig` through the real device wrapper into a
recording UHD mock: sources are set first, then each configured export Boolean
is passed exactly. The alternate export array is a software forwarding control,
not a recommended live configuration or new hardware run.

This expectation has more than a passing-test rationale: the user's stated
configuration is recorded in the tracker, and
`/home/u/phase-calibration/docs/twinrx_lo_sharing_readme.md`, September 11 at
14:39 UTC, retains two 20-second A-master captures without Board B's return
export. Those bounded historical observations do not certify the current
calibration vector or prove universal superiority of one export array.

The selected `x300_phase_offsets_added_hw_100khz.json` still records
`[true, false, true, false]`. That provenance was deliberately **not** relabeled
to match the current profile. Its applicability remains an open validation
question, requiring proper context checks and/or suitable new calibration
evidence. Updating this test does not resolve the calibration mismatch.

## Other findings in the attached review

No implementation changes were made to these paths in this task:

| Finding | Current check and boundary |
| --- | --- |
| Interrupted jammer-release timer | Reproduced in the actual evidence state machine with a synthetic frozen baseline: low evidence at 10.0 starts the timer; conversion error at 11.0 preserves it; low evidence at 12.1 releases protection. Numeric wrong-shape control resets it and remains active. The real caller's exception branch was inspected and also omits the reset. Still unresolved; no RF claim. |
| Calibration context and types | Actual `build_runtime_config()` accepts temporary calibration copies with 100 MHz/1 dB metadata, NaN uncertainty or string `"false"` quality. Boolean `false` is rejected. The selected original file was never edited. |
| Per-channel clipping hidden by aggregation | Actual helper gives 0.002 for the affected channel and 0.0005 for the four-channel aggregate, versus the configured 0.001 warning threshold. This reproduces the review's digital diagnostic limitation, not a measurement of analog overload. |
| Automatic-only control and truth-free CEP | Existing changes retained; applicable suite tests exercised. These do not fix the independent release or calibration issues. |
| Saved tone-frequency discrepancy, calibrated dBm meter, analog overload, tracking-loop settings and white-noise-gain policy | Not changed or newly hardware-verified here. The prior report/ledger qualifications remain, not a new claim that every third-party hardware assertion was independently revalidated. |

The executable probe is `review_probes.py` in the evidence directory. It uses
temporary calibration files and patches only the in-process default-profile
provider for those probes. It never writes the product calibration/profile.
The initial probe mistakenly referenced `rx_clip_fraction_warn` when printing
the final result and raised `AttributeError`; the schema's actual name is
`rx_clipping_fraction_threshold`. That auditor error is retained in
`review_probes.log`. The corrected script completed all assertions and emitted
`review_probes_corrected.log`. This was not an application defect or fix.

## Commands and results

Commands below were run from `/home/u/antijamming` with its `.aj` environment
(Python 3.12.3, pytest 9.1.1, NumPy 1.26.4, PyQt6 6.6.1).
All pytest runs used `QT_QPA_PLATFORM=offscreen`; no `usrp` test was selected.

1. Existing baseline selection:
   `.aj/bin/python -m pytest -q tests/test_jsonc.py::test_real_shell_profile_readers_accept_comments_without_site_packages tests/test_usrp_antenna_config.py::test_default_twinrx_lo_sharing_map_matches_two_board_layout`
   — **2 failed, 1 passed** (`baseline.log`, `baseline.xml`).
2. New launcher harness against the old launcher:
   `.aj/bin/python -m pytest -q tests/test_realtime_launcher.py`
   — **3 failed, 3 passed** (`launcher_before.log/xml`). The later Python-exception
   parameter was not yet present in this first six-case run.
3. Focused post-change selection:
   `PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q tests/test_jsonc.py tests/test_realtime_launcher.py tests/test_usrp_antenna_config.py`
   — **53 passed** (`focused_final.log/xml`); earlier 52-case run is retained.
4. Broad development/warnings gate:
   `PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q -m 'not usrp'`
   — **509 passed, 1 failed, 1 deselected** (`full_warnings.log/xml`). The
   32-source FIFO stress case passed its byte-count/hash assertions but exceeded
   its existing 250 ms whole-write latency assertion: maximum 315.15 ms.
5. Isolated repeat of that same FIFO case, with the same warning/development
   settings — **failed again**, maximum 599.69 ms (`fifo_repeat.log/xml`).
   No latency limit, FIFO implementation or test was changed to hide this.
6. `PYTHONPATH=src .aj/bin/python audit-results/launcher_lo_review_gce0zJ/review_probes.py`
   — the separate release/calibration/clipping findings reproduced as above.
7. Ruff, Vulture at 90% confidence, `compileall`, `bash -n run_realtime.sh` and
   `git diff --check` passed at the initial post-correction checkpoint.

8. Coverage gate:
   `.aj/bin/python -m pytest --cov=src/antijamming --cov-report=term-missing --cov-report=xml:audit-results/launcher_lo_review_gce0zJ/coverage.xml -q -m 'not usrp' --junitxml=audit-results/launcher_lo_review_gce0zJ/coverage_tests.xml`
   — **509 passed, 1 failed, 1 deselected**, **79% statement coverage**.
   The same FIFO latency assertion failed (maximum 578.01 ms); byte-count/hash
   assertions passed. Full output is in `coverage.log`.
9. Final Ruff, Vulture, compileall, Bash syntax and diff checks passed.
   `sha256sum --quiet -c audit-results/launcher_lo_review_gce0zJ/final_project_sources.sha256`
   verified the source/test/config manifest after the gates. Baseline/final Git
   branch refs compare identically. The profile, selected calibration and
   backend also retain their baseline hashes.

At 2026-09-17T17:05:02Z, the requested launcher and LO-test corrections have
focused software evidence. The broader suite is **not all green**; the FIFO
timing failure has not been causally diagnosed. An observed host load average
is not proof of the failure's cause. Passing the two repaired boundaries does
not establish general system correctness. No commit, push, Spark synchronization,
hardware action or archive replacement was performed.

## Follow-up: launcher necessity and stale script assumptions

2026-09-17, audit only, same cleanup HEAD and working-tree context. The user
asked whether the launcher was needed and whether scripts contain stale code;
this did not authorize another product edit or setup/hardware execution.

### Scope and active contracts

All six project-owned Bash scripts were read completely. Their current callers
and downstream consumers were traced. The Python analysis tools were checked
for entry points, documented use and tests; this is **not** an exhaustive
semantic audit of their bodies or the vendored GNSS-SDR tools. Earlier combined
tool output was truncated; the affected source reads were repeated in smaller
calls before reporting these findings. ShellCheck was unavailable; `bash -n`
is syntax checking, not a substitute for it.

The launcher is useful for the supported GUI workflow, but is not the
beamforming algorithm. An external headless controller can supply equivalent
environment and lifecycle management without running this GUI launcher. The
UHD console log and GNSS startup-console settings have real consumers and were
not classified as dead merely because they are environment variables.

### Findings and disposition (no product changes)

| Finding | Evidence and classification |
| --- | --- |
| GNSS shutdown selector uses an obsolete process-group assumption | `run_realtime.sh:94` requires the receiver's PGID to equal the owned headless backend's PGID. Both `app/main.py` and `sdr_bridge/bridge.py` use `start_new_session=True` for their respective children; `parent_guard.py` execs the receiver without joining its parent's group. The actual selector returns no receiver for this modeled layout; the same-group positive control finds PID 5100. **Migrate ownership selection**, not blindly remove shutdown protection. The bridge's own stop handling and parent-death signal remain: this probe does not establish a real orphan or test every shutdown schedule. |
| Setup verifies the wrong calibration selection | `setup.sh:12` hard-codes `x300_phase_offsets_100khz.json`; the parsed current profile selects `x300_phase_offsets_added_hw_100khz.json`. **Migrate** setup to validate the selected file. This finding does not authorize deleting either file or establish that either calibration is physically applicable. |
| UHD discovery is incompletely migrated to source-built runtime | `find_x300_at_addr()` and `resolve_x300_host_link()` use bare `uhd_find_devices` and retain the `uhd-host did not install correctly` message. Setup runs the build helper as a child; its environment exports do not activate the parent's PATH. Parent activation occurs only in the final `verify_setup()`. Earlier discovery can therefore find a different tool or none unless the invoking environment already selects the intended one. Later image-loader paths explicitly select the source install. **Migrate** the earlier discovery/check/message; no fresh install or device probe was performed. |
| Sidecar GNSS attribution is not session-specific | `discover_gnss_pid()` selects the first matching receiver executable/config basename under this repository, without checking backend ancestry; equivalent selectors are repeated in monitoring loops. A concurrent receiver can be attributed to the wrong session. **Migrate** ownership handling; this is a code-path risk, not a newly observed concurrent-run failure. |
| Historical desktop address | `run_realtime.sh:220` embeds `10.189.184.209:3389` in generic missing-display guidance. **Remove or migrate the machine-specific hint**, not desktop validation. No claim is made about whether that address is currently reachable. |
| Matplotlib environment setting | `run_realtime.sh:199` exports `MPLBACKEND`; `requirements.txt` lists matplotlib, but no consumer was found in project `src`, `tests` or `tools` searches. **Removal candidate for the current application**, with external/manual/vendored analysis usage still outside the proven boundary. The Qt/PyQtGraph display setup is separately active. |
| Duplicate profile/route helpers | Launcher and sidecar repeat address/interface discovery; the sidecar fallback is callable when used on its own. **Defer consolidation**: duplication is not proof that the fallback is unused or safe to delete. |
| Direct dependency declarations | The UHD build helper requests Ninja and the sidecar calls `pidstat`, `mpstat`, `iostat`; setup does not explicitly declare `ninja-build`/`sysstat` or validate those monitor commands. **Dependency-audit follow-up**, not proof that they are absent on this laptop or cannot arrive transitively. |

Rejected initial suspicion: the GNSS VOLK profiler path is **not proven stale**
just because setup lacks a top-level install command. Vendored
`gnss-sdr/CMakeLists.txt:1466` has a post-build copy into the local install
directory when building its own VOLK module. External-VOLK and fresh-build
variants were not exercised; no profiler removal or general setup guarantee
follows from this source check.

The standalone tools also have distinct consumers: `mark_rf_event.py` records
operator observations without commanding RF/LCMV; `summarize_lcmv_run.py` and
`audit_live_session.py` support documented evidence review and tests;
`usrp_source_count_snapshot.py` is an explicitly invoked hardware diagnostic
with receive-contract tests. Not running every helper during normal startup is
not grounds to delete it. This audit did not certify every line of those tools.

### Reproducible checks and limitations

Evidence remains in the existing `audit-results/launcher_lo_review_gce0zJ/`:

- `scripts_audit_probe.py` extracts only the two actual PID-selection shell
  functions and supplies fake `ps` records, including an unrelated receiver.
  It never sources a full launcher/setup or runs a kill/stop function. It also
  compares the actual parsed profile calibration selection with setup's literal,
  checks all six shell files with `bash -n`, and records their SHA-256 hashes.
- `PYTHONPATH=src .aj/bin/python audit-results/launcher_lo_review_gce0zJ/scripts_audit_probe.py`
  completed all assertions; output is in `scripts_audit_probe.log`.
- `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q tests/test_realtime_launcher.py tests/test_uhd_runtime_env.py tests/test_jsonc.py --junitxml=audit-results/launcher_lo_review_gce0zJ/scripts_audit_tests.xml`
  produced **44 passed** (`scripts_audit_tests.log/xml`). These passing tests do
  not cover the new process-group/calibration findings and do not negate them.

Only audit evidence and these living documents were added/updated. No product
script, profile, calibration, branch reference, dependency installation,
hardware, remote repository or distribution archive was changed. The previous
full-suite FIFO timing failure and other unresolved findings remain open.

## Scoped stale launcher cleanup — September 18

At `2026-09-18T06:07:25Z`, the two smaller candidates have been resolved in the
laptop cleanup working tree. Baseline HEAD remains `dfd705a`; the preceding
dirty edits were preserved. Evidence is in
[`audit-results/launcher_stale_removal_gIwsGY/`](../../audit-results/launcher_stale_removal_gIwsGY/).
This scope does not include the other setup/shutdown findings above.

### Consumer evidence and decisions

- **Remove the launcher's `MPLBACKEND=Agg` default, not Matplotlib itself.**
  The project UI's algorithm plots, PRN monitor and skyplot import PyQtGraph.
  Searches of current `src`, `tools`, `configs` and entry scripts found no
  Matplotlib consumer other than the environment assignment being removed.
  The launcher creates the GUI, its headless service and diagnostics; the GNSS
  bridge launches the receiver executable through its parent guard, not any
  vendored Python plotting utility. Removing this assignment leaves the Qt
  platform selection and all other startup/lifecycle operations unchanged.
  An inherited user `MPLBACKEND` value is left alone, not forcibly cleared.
- **Retain `requirements.txt`'s Matplotlib entry and vendored analysis tools.**
  Expanded search found concrete users in `gnss-sdr/utils/skyplot/skyplot.py`,
  `utils/osnma-log-viewer/osnma_log_viewer.py`, tracking/navigation/observables
  plot helpers and the VOLK comparison plot. The skyplot README explicitly
  lists the dependency. These separately invoked tools are not the product's
  live skyplot widget. No claim that Matplotlib is unused throughout the whole
  repository, or permission to uninstall it, follows from the GUI finding.
- **Replace the historical desktop address in the missing-display message.**
  `10.189.184.209:3389` was only literal help text in this launcher, not an
  input to device discovery or networking. It now advises a local/remote
  graphical desktop or explicit offscreen diagnostics. The real missing-X11-
  display test still exits with status 2 before GUI/sidecar creation.
- **Retain active display checks and ordinary Qt diagnostic messages.**
  They concern opening the GUI, not jammer detection. The user's follow-up
  asks what they mean, not for removal of functioning startup checks. Routine
  technical output could separately be simplified; it is not unused code.

The only product change is this two-location edit in `run_realtime.sh`.
`scope_check.log` records an exact baseline-to-current string transformation
assertion: remove the single export line and replace the single old echo with
the two generic guidance lines. It also checks unchanged requirements, profile
and backend hashes. Test fixture changes capture only the selected plotting
environment keys, not credentials or the full environment.

### Verification and exact boundaries

All commands below ran from `/home/u/antijamming` with Python 3.12.3.
No setup, UHD activation against real hardware, real application/receiver
launch, dependency installation, remote operation or archive refresh occurred.

1. Before product edits, new cases in `tests/test_realtime_launcher.py`:
   `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q tests/test_realtime_launcher.py -k 'matplotlib or machine_independent' --junitxml=audit-results/launcher_stale_removal_gIwsGY/before.xml`
   — **2 failed, 1 passed, 7 deselected** (`before.log/xml`). The absent
   environment acquired `Agg`, and the error contained the old machine's
   address. The explicitly inherited `svg` backend is the passing control.
2. After removal, `QT_QPA_PLATFORM=offscreen PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q tests/test_realtime_launcher.py tests/test_uhd_runtime_env.py tests/test_jsonc.py --junitxml=audit-results/launcher_stale_removal_gIwsGY/focused.xml`
   — **47 passed** (`focused.log/xml`). The real shell and JSONC parser run
   against disposable GUI/UHD/route/process/sidecar stubs; no RF is involved.
3. `QT_QPA_PLATFORM=offscreen PYTHONPATH=src PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python audit-results/launcher_stale_removal_gIwsGY/without_matplotlib.py -q -m 'not usrp' --junitxml=audit-results/launcher_stale_removal_gIwsGY/without_matplotlib.xml`
   — **513 passed, 1 hardware test deselected**, **zero Matplotlib import
   attempts and zero loaded Matplotlib modules** during the gate. The retained
   runner first proves the blocker works with an explicit rejected import.
   It removes `MPLBACKEND` only in its own audit process. Its import hook covers
   the test interpreter, including exercised real GUI widget code, not child
   processes, unexercised optional paths or separately invoked vendored tools.
4. `.aj/bin/python -m pytest -q -m 'not usrp'` with offscreen Qt and JUnit
   output `full.xml` — **513 passed, 1 deselected** (`full.log/xml`).
5. The same full selection with `PYTHONDEVMODE=1 PYTHONWARNINGS=error`,
   `--cov=src/antijamming --cov-report=term`, coverage XML `coverage.xml`, and
   JUnit `coverage_tests.xml` — **513 passed, 1 deselected**, **79% statement
   coverage** (`coverage.log`). Both are fresh runs after the product edit.
6. `.aj/bin/ruff check .`, `.aj/bin/vulture src tests --min-confidence 90`,
   `.aj/bin/python -m compileall -q src tests`, separate `bash -n` checks of
   all six project shell scripts, and `git diff --check` passed. ShellCheck
   was unavailable, not silently substituted by a claim of full shell analysis.

Source/reference inventories, baseline launcher/tests/requirements, current
dirty patch, before/after hashes and branch references are retained alongside
these results. Earlier combined tracker/source output was truncated; the
missing final tracker section was reread separately. A lookup of nonexistent
`pyproject.toml` was corrected to the actual `pytest.ini` and `ruff.toml` files.

This establishes the scoped environment/message change under the named
software conditions, not that every possible code path is free of stale
behavior. The previously observed FIFO timing failure did not recur in these
runs; it is **not diagnosed or fixed** by removing display-related lines.
Jammer-release, calibration, setup discovery and shutdown ownership findings
remain open. Main/experimental references are unchanged; no commit or push.

## Actual Tramiq SSH launcher check — September 18

The user requested an actual SSH invocation to establish whether the launcher
detects remote access or only checks display settings. Initial connection
attempts failed: alias `tramiq` resolved to unavailable `spark-7e4d.local`, and
the known address initially returned `No route to host`. A subsequent user-
requested ping received 4/4 replies; SSH then succeeded using a per-command
`HostName=192.168.3.153` override. No SSH configuration was edited and no X11
forwarding or display variable was enabled/disabled for the remote run.

At `2026-09-18T06:21:02Z`, the actual remote repository was
`/home/tramiq_sdr/antijamming`, clean cleanup branch at
`6b45b24b43d2e215c687ffabe185e679be206d08`, **not** the laptop's newer dirty
`dfd705a` checkout. The full remote launcher, UHD environment selector, repository
instructions and 541-line tracker were read before execution. The remote
launcher SHA-256 before and after was
`9bb0575b554198b243cc349ed63f7945982298e8b22d548017ba01760806c57e`.

Invocation through ordinary SSH, with no application arguments:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 -o HostName=192.168.3.153 tramiq
# In /home/tramiq_sdr/antijamming, executed as a remote command:
timeout --signal=TERM --kill-after=3s 15s ./run_realtime.sh
```

Actual source behavior in both inspected copies: choose Qt's display backend
from `QT_QPA_PLATFORM`/`WAYLAND_DISPLAY`; in the ordinary X11 branch reject an
empty `DISPLAY`. This is an environment-presence precheck, not verification of
display-server reachability or permissions. No `SSH_CONNECTION`, `SSH_CLIENT`
or `SSH_TTY` detector was found in the inspected `src`, `tools` and launcher.
The check does not decide local versus remote access, enable forwarding, or
attach a window to an existing desktop. `Qt platform` informational output is
later in the script and was not reached in this failed invocation.

Raw source, environment, output/status and searches are retained under
[`audit-results/ssh_display_check_EroiLW/`](../../audit-results/ssh_display_check_EroiLW/).
`actual_launcher.log` SHA-256 is
`d030ec5d2c0112749fb257b53910b89f480035084b58b9bb3b4609dd3519d5cd`.
Remote `rg` was absent, so later source searches used `git grep`. A later ref
inventory included an absent local `per-prn-fifo-experimental` reference and
failed after printing HEAD/main; no experimental-branch comparison is claimed.
Only local evidence/docs were added; no remote source/config/branch update,
receiver launch, forwarded-display test, commit, push or archive refresh.

Follow-up distinction: `app/main.py` creates a Qt application and starts
`app/headless.py` as a separate service. Offscreen mode changes the former's
display integration; direct headless operation omits that GUI process entirely.
The headless service owns `BackendRuntime`, accepts local IPC commands, and
starts idle unless `--auto-start` requests hardware startup. Its direct entry
does not run the GUI shell's display check. Selecting the appropriate UHD
environment is still necessary for actual radio operation.

Two existing software tests passed in `headless_contract.log`. The named
no-Qt-import test reimports an already imported module and is not independently
strong cold-import evidence. A separate fresh interpreter with PyQt6/PySide6
imports blocked then imported the actual headless module with zero Qt attempts
(`headless_fresh_import.log`). The start/stop check uses a fake backend. These
checks and source tracing establish their stated boundaries, not a new live
headless hardware run. No product changes were made.

## Operator-facing display messages — September 18

At `2026-09-18T06:40:34Z`, the user requested that ordinary terminal output not
explain X11/Qt/offscreen internals. This follow-up supersedes the earlier
decision to retain those informational prints; it does not remove the active
display checks. Scope is the laptop cleanup working tree, still at `dfd705a`
plus its existing edits, not Spark or another branch.

The only product edit is in `run_realtime.sh`: replace both missing-display
messages with plain-language window/desktop guidance and remove the routine
`Qt platform` and `Display target` prints. Selection of explicit/default Qt
platform, environment handoff, missing-display exit status 2, logging choice,
route lookup, subprocess creation and shutdown remain unchanged. A missing
desktop must still produce an actionable error; silently launching an invisible
GUI is not the new default. Other driver/receiver/diagnostic output has not
been globally suppressed or redesigned.

The current GUI already records `app.platformName()` in `logs/app.log` when
logging is enabled. No new log sink or verbosity parameter was introduced.
`app/main.py` creates the Qt GUI plus a separate `app/headless.py` service;
the latter owns `BackendRuntime` and a local command socket. Direct headless
operation requires neither a desktop nor Qt offscreen mode. An offscreen GUI
test is a different path that still constructs the GUI. These existing
boundaries are documented in `docs/realtime_gui.md`, not newly implemented.

### Retained evidence

[`audit-results/launcher_messages_C8bu5J/`](../../audit-results/launcher_messages_C8bu5J/)
contains baseline launcher/tests, before/after source hashes and branch refs,
the preexisting dirty patch, this batch's launcher/test patches and raw test
output/JUnit reports. From the repository root:

- Before product edits:
  `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q tests/test_realtime_launcher.py -k 'plain_language or without_printing' --junitxml=audit-results/launcher_messages_C8bu5J/before.xml`
  — **9 failed, 9 deselected**. Three rejection cases still used jargon;
  six successful platform-selection cases still printed internals.
- After edits:
  `QT_QPA_PLATFORM=offscreen PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q tests/test_realtime_launcher.py tests/test_uhd_runtime_env.py tests/test_jsonc.py --junitxml=audit-results/launcher_messages_C8bu5J/focused.xml`
  — **55 passed**. Missing X11/Wayland/default display cases reject before
  child or route creation; explicit offscreen/minimal/X11/Wayland and automatic
  X11/Wayland selections reach the child with their expected environment.
- `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q -m 'not usrp' --junitxml=audit-results/launcher_messages_C8bu5J/full.xml`
  — **521 passed, 1 hardware test deselected**.
- `QT_QPA_PLATFORM=offscreen PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q -m 'not usrp' --cov=src/antijamming --cov-report=term --cov-report=xml:audit-results/launcher_messages_C8bu5J/coverage.xml --junitxml=audit-results/launcher_messages_C8bu5J/coverage_tests.xml`
  — **521 passed, 1 deselected**, **79% statement coverage**.
- `.aj/bin/ruff check .`, `.aj/bin/vulture src tests --min-confidence 90`,
  `.aj/bin/python -m compileall -q src tests`, `bash -n` on all six project
  shell scripts, and `git diff --check` passed.

The launcher harness runs the actual shell and JSONC reader but substitutes
UHD activation, GUI, route/process discovery and sidecar commands. Nonempty
display values are placeholders, not real desktop connection tests. The full
software suite exercises real Qt widgets offscreen and fake radio interfaces;
it is not hardware evidence. The unchanged-boundary hash check covers GUI and
headless entry points, backend, UHD environment helper and runtime profile.
Checking the entire baseline manifest also reports the two intentional
launcher/test changes as mismatches; those are not unexplained mutations.

Earlier combined source/tracker output was truncated; the missing tracker
passage was reread separately. Source inspection remained targeted, not a new
whole-repository semantic audit. Previously recorded FIFO timing, jammer
release, calibration and ownership findings remain open. No commit/push,
remote operation, archive refresh or hardware test occurred in this batch.

## Follow-up: deployment and log retention — September 18

At `2026-09-18T06:43:36Z`, the user asked whether production needs another
branch and whether GUI/headless logs are truncated. This is an inspection,
not authorization to change logging policy, delete archives or create branches.

- Current profile `logging_enabled` is **true**. Both application entry points
  call `setup_logging(..., enabled=cfg.logging_enabled)`, whose enabled file
  handlers initially append. Merely opening the app is not a full log reset.
- `BackendRuntime` calls `reset_session_logs()` on stream Start when logging
  is enabled. It recovers a pending old session, truncates the named root log
  files and creates a new `logs/runs/<session_id>` archive directory. At Stop,
  `finalize_session_logs()` copies the current logs into that directory and
  retains the root logs. The next Start clears the current set, not old runs.
- GNSS-SDR working directories are cleared on bridge startup, while selected
  receiver artifacts are copied into the owned run archive. Tracking JSONL is
  also written into the run archive. This is not a zero-storage receiver path.
- The GUI shell separately truncates `uhd_console.log` at launch when enabled.
  Its optional sidecar moves the previous `current` directory into timestamped
  `logs/sidecar/runs` storage. Direct headless entry does not run that shell or
  start its sidecar, but does use the same backend session lifecycle.
- The named application handlers have no size rotation. Source searches found
  no age/count/total-size pruning of the run or sidecar archives. Therefore
  archives can accumulate and an uninterrupted run can keep growing. These
  paths are absent in this laptop checkout's current `logs` directory; this
  is a code-level growth finding, not a measured disk-full condition.
- `logging_enabled=false` disables the named file loggers, including
  `errors.log`, plus optional session/receiver persistence. It is not merely
  a UI verbosity switch. Live GNSS stdout parsing, UDP/NMEA state and FIFOs
  remain needed; disabling their persistence must not remove live consumers.

Eight existing focused logging/reset/archive/recovery/receiver-state tests
passed with `QT_QPA_PLATFORM=offscreen PYTHONDEVMODE=1 PYTHONWARNINGS=error`;
the exact test names and results are retained in
`audit-results/launcher_messages_C8bu5J/log_lifecycle.xml` and `.log`. They use
temporary directories and simulated receiver records, not live RF or a disk-
exhaustion test. The relevant 370-line logging implementation was read fully;
backend/bridge/entry-point/sidecar consumers were inspected at their relevant
boundaries. Truncated combined output was recovered with smaller reads.

Recommendation, not implementation: retain one product implementation, develop
changes on working branches and deliver explicitly tested release revisions.
Operator-facing messages and developer evidence should differ by logging
policy, not divergent algorithms. Bounded operational/error logs and optional
detailed diagnostics would be a separate production-retention change. No such
policy, branch promotion or default switch was added in this inspection.

## Desktop-only operator launcher — September 18

The user's next instruction supersedes the plain-language missing-display
warning: normal operator use is from the desktop, with explicit offscreen
settings reserved for developers. The scoped semantic-cleanup review removes
the custom display precheck and its now-unreferenced `qt_platform_name()`
helper, the shell's window-troubleshooting hint, and the Python GUI's terminal
visibility confirmation. The GUI's platform/geometry file-log record stays.
Explicit platform selection, automatic desktop-platform selection, application
exit propagation, process ownership and headless processing are retained.

This does change the missing-display failure boundary: the launcher no longer
rejects before route/child startup. Qt initializes before the GUI creates its
headless service and handles its own display failures. The launcher still
returns the child's failure status; it neither selects offscreen implicitly
nor suppresses native errors. No claim that ordinary SSH can now display a GUI
or that every startup failure is silent follows from removing our warnings.

Evidence directory:
[`audit-results/launcher_desktop_only_ZUQhLZ/`](../../audit-results/launcher_desktop_only_ZUQhLZ/).
Baseline copies, hashes, preexisting dirty patch, before/after refs, product
diffs and raw logs/JUnit results are retained. Commands from the repo root:

- `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q tests/test_realtime_launcher.py`
  before product edits: **13 failed, 9 passed**. These include old precheck
  exits instead of the child status, troubleshooting text and the GUI print.
- With `QT_QPA_PLATFORM=offscreen PYTHONDEVMODE=1 PYTHONWARNINGS=error`,
  `.aj/bin/python -m pytest -q tests/test_realtime_launcher.py tests/test_uhd_runtime_env.py tests/test_jsonc.py`:
  **59 passed**. The shell harness uses fake GUI/UHD/process/route/sidecar
  boundaries; the GUI print/log test inspects its AST, not a live window.
- `QT_QPA_PLATFORM=offscreen .aj/bin/python -m pytest -q -m 'not usrp'`:
  **525 passed, 1 hardware deselected**. Repeat with development/warnings-as-
  errors and `--cov=src/antijamming --cov-report=term`:
  **525 passed, 1 deselected**, **79% coverage**. Named XML outputs are in
  the evidence directory. No actual receiver or desktop connection was tested.
- Ruff, Vulture >=90%, compileall, six Bash syntax checks and diff check passed.
  Headless/backend/profile/UHD helper hashes and all four branch tips remain
  unchanged. The GUI source diff removes only its visibility print.

Historical display checks and their earlier test results remain evidence,
not current instructions. Active GUI documentation/tests were migrated. All
other documented stale-code, timing, calibration and jammer findings remain
open; this is not a whole-repository cleanup claim. No hardware, remote source,
logging-policy, dependency, archive-package, commit or push changes occurred.

### Complete Git-reference inventory

At `2026-09-18T07:01:40Z`, a follow-up reachability inspection found **57
distinct historical commits** reachable through archive/original refs but not
through current local/remote-tracking branches. This counts commit identities,
not 57 missing features; rewritten history can preserve equivalent file content
under different hashes. Per-archive counts (overlapping, not additive) are in
`archive_reachability.txt`, produced with
`git rev-list --count "$archive_ref" --not --branches --remotes`. The union
count uses `git rev-list --count --glob='refs/archive/*' --glob='refs/original/*' --not --branches --remotes`.
Six of seven archive tips retain such historical ancestry. The pre-latch main
archive equals current `main`, and all three original refs duplicate archive
tips. No backup bundle, independent off-machine copy or semantic equivalence
of those histories was verified here. Retain for recovery/provenance; any
future consolidation needs a verified backup, not blind deletion. No refs or
runtime code changed. The display-change source manifest still matches its
tested state; no new software/RF test was run for this Git-only inspection.

## Headless log lifecycle and integration boundary — September 18

At `2026-09-18T07:08:16Z`, the current headless service, logging lifecycle,
backend Start/finalizer, IPC disconnect handling and GUI command adapter were
inspected for the user's follow-up. No service, receiver or test was started.

- Launching `app.headless` reads the fixed runtime profile once and creates
  append-mode loggers if enabled. Missing files are created. Service creation
  alone does not truncate the previous run's named logs. Without `--auto-start`
  it waits idle for a controller; that flag immediately requests a receiver run.
- An accepted new receiver Start calls `BackendRuntime.run()`, which resets
  named root logs and opens a new archive session when logging is enabled.
  Normal GUI Start and headless IPC Start converge on this same path. Repeated
  Start while already running is rejected as a new run, not a log reset.
- Stop requests asynchronous backend shutdown. Backend finalization archives
  logs, and its monitor then reports idle. Stop is not service exit, archive
  deletion or automatic age/size rotation. A subsequent Start reuses the root
  filenames but creates another archive directory. Existing archives remain.
- `shutdown`, SIGINT and SIGTERM request service teardown and backend Stop;
  hard termination cannot execute finalization. The next enabled Start attempts
  pending-session recovery before truncation. This is not a guarantee against
  power loss, disk errors or forced termination losing unflushed data.
- Logging false skips optional persistence, not live GNSS state parsing. It
  does not delete previously saved files. Changing the profile requires
  restarting the service to reload it; receiver Stop/Start alone retains the
  already constructed configuration. Relative log paths belong to the actual
  anti-jamming source repository through `_anchor_runtime_log_paths()`.
- Current local `main` has unconditional `setup_logging(cfg.log_dir)` and
  unconditional backend log reset, without cleanup's `logging_enabled` switch.
  Its reset/archive lifecycle exists, but cleanup's switch must not be assumed
  available in an older integration checkout.
- IPC client disconnect closes/discards that connection; it does not request
  backend Stop or service shutdown. A controlling frontend must explicitly
  manage these commands/lifecycle. The inspected standalone `RemoteStreamWorker`
  sends `start`, `stop` and `shutdown`; metrics/status travel back over the
  local socket. That establishes the interface, not the current TramiqSDR
  controller's exact tab-close or process-output-file behavior.

No TramiqSDR source was found among the laptop home-directory project entries.
A bounded read-only SSH probe of `qvise@192.168.3.156`, using BatchMode and
strict host-key checking, failed with `No route to host`. Therefore the current
remote integration revision, whether it sends Stop on tab/window close, and
whether its own captured stdout file appends/truncates remain unverified.
Earlier notes describe integration intent, not evidence for that running copy.
No product configuration, source, logs, branch or remote state was modified;
only these evidence notes were extended. No fresh software/hardware test claim.

## NADS 2 controller and installed logs — September 18

Read-only remote inspection on `2026-09-18`, approximately 07:32–07:39 UTC:
`qvise@192.168.3.127`, hostname `spark-8219`. The user's subsequent instruction
explicitly prohibits NADS 2 changes. No remote source/config edit, build,
installation, log deletion, receiver command, GUI interaction or service
start/stop was performed. Existing applications exited during observation;
these were not terminated by this investigation. Read-only `/proc` access used
sudo because the GUI was running as root. No credentials were stored.

Local changes only: SSH alias `nads2` in `/home/u/.ssh/config`, preserving
`nads` as `.154`; this record, the existing progress tracker and the UX boundary
in `docs/realtime_gui.md`. `nads2` uses the already verified host key saved under
the same machine's former `.222` address, with strict host checking. Source
snapshots and command outputs are retained under
[`audit-results/nads2-controller-z3GJaW/`](../../audit-results/nads2-controller-z3GJaW/).
`source-snapshots.sha256` identifies each inspected copy. No current product
runtime code or logging setting changed on the laptop either.

### Actual source and deployment identities

- `/home/qvise/Desktop/qvise/tramiqsdr` and `/home/qvise/tramiqsdr` are distinct
  checkouts at `9e6eed5b4bab0005647dd66f226fa1e331ea5172` with existing dirty
  GUI/settings/tests and an untracked Chrony/CSV-throttling test. Their source
  paths have different inodes. Those changes were not authored here.
- Desktop anti-jamming is clean at `d8aef5ff2d9ca6e335a355d1d98e07b80aa12300`.
  The separate installed app lives at
  `/home/qvise/.local/share/tramiqsdr/runtime/`. Its desktop shortcut launches
  `/home/qvise/.local/share/tramiqsdr/app`, not the repository launcher.
- The actually observed GUI PID 155216 instead had working directory
  `/home/qvise/Desktop/qvise/tramiqsdr`, command
  `python/venv/bin/python python/tramiq_sdr.py .`, Python executable
  `/usr/bin/python3.12`, and stdout/stderr `/dev/pts/8`. Its Chrony child also
  used that checkout. Thus this observation was not the installed GUI and
  was not writing stdout through the installed launcher's `tee` pipeline.
- Controller and panel snapshots match byte-for-byte between checkout and
  installed copy. Logging `setup.py` also matches desktop anti-jamming and
  installed anti-jamming. The main GUI scripts do not match: source SHA-256
  `787b188bcd2a69e3f63d1b81e366c5a4129c12bcb68387bde02f7004f3638071`,
  installed `7230a53383ba14fe3442cede9035266de7946990d14df162eb36f7c9bd78e05c`.
  Whole-repository/runtime equivalence is not claimed from these comparisons.

### Complete inspected logging contract

The retained `installed-controller/service_controller.py` implements:

1. `start()` requests startup; the supervisor resets RX2/fused PVT CSVs on its
   own thread. `_launch_service()` either attaches to an existing backend or
   spawns `antijamming.app.headless`, then sends IPC `start`.
2. Owned launches open `repo/logs/tramiq_headless_service.log` in **append**
   mode (line 1067) for merged stdout/stderr. This file is not in anti-jamming's
   `LOGGER_DEFS` or auxiliary-reset list. Receiver Start therefore does not
   truncate this capture.
3. Installed `headless.py:199` creates append-mode named loggers unconditionally.
   Its IPC Start reaches backend `run()`, which calls `reset_session_logs`
   (`backend.py:477`). That function recovers unfinished previous evidence,
   truncates/reopens the named current logs and creates `logs/runs/<session>`.
   It does not remove previous run directories.
4. Owned Stop sends `stop` and `shutdown`, waits on the supervisor thread and
   applies its existing process-group teardown logic. Backend finalization
   (`backend.py:778`) copies named logs to the run directory. Normal Stop is
   asynchronous to Tk; `close()` has a bounded waiting path. If attached to a
   standalone anti-jamming GUI, controller Stop deliberately disconnects
   without stopping that separately owned backend. Changing tabs is not Stop.
5. Installed `launch.sh:123` uses `tee -a` for a separate
   `~/.local/state/tramiqsdr/tramiqsdr.log`. Its header/error writes also append.
   No size rotation or archive pruning was found in these inspected owners.

The actual installed `app.log` starts at the latest session's September 10
13:52:44 local time, while earlier sessions retain different starts. The latest
manifest is finalized with stop reason `headless service shutdown`. This is
historical evidence consistent with reset/archive behavior, not a new live
Start/Stop test. Its failed USRP discovery and older capture's Radio-block
compatibility errors are historical; they are not today's measured lag cause.
The installed logging code has no cleanup-branch `logging_enabled` switch.

### Measured storage and why rebuilding can retain it

Read-only `du`, `stat` and directory inventory found:

| Location | Observed size / state |
| --- | --- |
| Installed anti-jamming `logs/` | 276 MiB total; about 273 MiB in 60 saved run folders |
| Desktop checkout anti-jamming `logs/` | 733 MiB total |
| Installed TramiqSDR state directory | 42 MiB; `tramiqsdr.log` 43,118,607 bytes, last modified September 10 |
| Installed controller stdout capture | 1,500 bytes, last modified September 7 |
| Installed TramiqSDR `output/` | 15 MiB |
| Desktop TramiqSDR `python/output/` | 3.6 MiB |
| Chrony CSV, installed / desktop | 8,926,613 / 10,383,750 bytes |

These are filesystem observations, not current writing rates or measurements
of NADS 1's reported eight GB. Approximately 109 GiB RAM was available, swap
use was zero and disk space was 23% used in the initial snapshot.

The relevant installer is the parent project's
`/home/qvise/Desktop/qvise/packaging/build-standalone-app.sh`, not the separate
TramiqSDR AppImage script. Its staging function creates an empty anti-jamming
log directory and copies source/config/receiver artifacts. Installation
(lines 337–348) explicitly excludes existing `/runtime/antijamming/logs/` and
`/runtime/tramiqsdr/output/` from replacement/deletion. The separate state log
is outside that install destination. Therefore a rebuild/reinstall using this
script preserves accumulated operator logs; it is not a retention mechanism.
The historical build invocation and NADS 1 installation were not inspected.
Do not claim the build itself generated or copied eight GB of logs.

### Lag: findings versus unproven cause

- The anti-jamming panel obtains `latest_panel_metrics()` from controller
  memory populated by the IPC reader. It does not reread `app.log` or saved
  run archives to render charts. Removing old archives is not an established
  solution for this lag.
- Panel `update()` redraws sky/CN0/LCMV/DoA even when the cached sequence has
  not changed. It emits rate-limited diagnostic terminal text. The main Tk
  `on_pages_change()` directly invokes selected-page rendering; the periodic
  Tk callback also performs receiver/data/plot work. These are inspected cost
  paths, not timing measurements or proof that this panel caused the delays.
- The installed GUI's Chrony helpers use whole-file `readlines()` on CSV data.
  The dirty desktop source already has bounded-tail/cache changes; they were
  present before this audit. Other conditional GUI-thread file reads and
  plotting remain. No regression or improvement claim is made for those
  independently authored edits, and changing GNSS timing semantics is not
  authorized by an interface responsiveness discussion.
- Initial `ps` snapshots showed GUI lifetime-average CPU approximately 526%
  and 570% and RSS about 5.3–5.5 GiB. Lifetime CPU includes initialization and
  is not steady-state tab latency. A later read-only `pidstat` sample of PID
  155216 showed 21–22% CPU in its first three one-second samples, no storage
  reads, and 0/0/244 KiB/s writes. It then exited during the final samples,
  leaving the intended five-sample observation incomplete. Its transient
  FD snapshot showed no open anti-jamming log; no headless/receiver process
  appeared in the inspected process snapshots. This does not prove such a
  process was never active or exclude cached file reads between snapshots.
- No GUI event-loop latency trace, Python/native stack profile correlated
  with slow tab switches, controlled A/B run, or stable long-duration sample
  was obtained. The exact lag cause remains **unresolved**. We did not open
  another GUI, trigger radio Start, kill existing processes, install a profiler
  or edit the running code to obtain one. At 07:38 UTC no Python GUI appeared
  in the final process snapshot; that observation is time-specific.

Verification is source/call-site inspection, exact snapshot hashing, historical
log/manifest comparison and bounded live resource observation. No receiver
test suite, functional GUI test, hardware run or remote correction was made.
Large combined displays were sometimes truncated; source/control reads were
recovered in smaller chunks and full remote stdout was retained through `tee`.
Long calibration-manifest fields are retained but not part of the semantic
review. Not every source line or historical log was reviewed. Bounded log
retention and responsive operator/developer UX remain explicit follow-up work,
not completed fixes.

## NADS 2 UHD setup and RFNoC mismatch — September 18

At 07:48 UTC, the user's follow-up requested inspecting `setup.sh` first,
checking the reported RFNoC failure, and conditionally publishing missing
changes with typed commits before rerunning setup. This follow-up remained
diagnostic: no remote install, network edit, image download/flash or application
source change was made. No Git commit or push occurred.

### Reproduced log evidence versus current connectivity

The desktop controller's 2,888-byte service capture includes repeated:

```text
Major compat number mismatch for 0/Radio#0: Expecting 0, got 1.
FPGA component `0/Radio#0' is revision 1 and UHD supports revision 0.
```

The corresponding `errors.log` at September 18 10:38:34 (+0300 host time)
records failure constructing `uhd.usrp.MultiUSRP` from `UsrpRxDevice`, ending
in `Failure to create rfnoc_graph`. This establishes the logged driver/FPGA
Radio-block mismatch before anti-jamming DSP starts. It does not identify the
exact loaded FPGA release or implicate jammer/beamforming code. The capture
also contains earlier firmware communication timeouts; the untimestamped
console lines do not supply an exact time for each event.

At 07:47:37 UTC a separate bounded read-only command was run:

```bash
timeout 12 /usr/bin/uhd_usrp_probe --args addr=192.168.40.2 --init-only
```

It reported UHD 4.6 and **No devices found**, with probe exit status **255**.
Both physical Ethernet interfaces were DOWN/NO-CARRIER; the route to
192.168.40.2 went via 192.168.3.2 on Wi-Fi. This is not a fresh reproduction of
the RFNoC mismatch. It prevents current loaded-image verification and is a
separate prerequisite for any deployment. The first capture omitted probe
stderr; `uhd-probe-followup.txt` explicitly captures it. A subsequent remote
`rg` inventory search failed because that command is unavailable there
(combined shell exit 127); the inventory was already captured successfully
with `grep`. Neither tool limitation is a receiver result.

### Confirmed setup control-flow gap

In the retained NADS 2 main script:

1. `install_system_dependencies()` requests unversioned `uhd-host` and
   `python3-uhd`; packages already installed are skipped. It does not guarantee
   a particular UHD release or upgrade existing packages.
2. `ensure_uhd_images()` (line 214) returns if the HG file exists. It does not
   run the selected UHD downloader's manifest check in that case.
3. `ensure_x300_hg_image_loaded()` (line 638) uses `uhd_find_devices`. At line
   657 it returns success solely if discovery says `fpga: HG`. It invokes the
   loader only when another FPGA flavor is reported.
4. `verify_setup()` checks imports, tool/file existence and the GNSS build,
   but does not initialize the USRP with `uhd_usrp_probe` to validate RFNoC
   compatibility. Thus HG discovery can pass despite a later init failure.

HG identifies Ethernet-port configuration, not the driver-compatible Radio
block revision. Downloading an image onto the computer and loading it into
the USRP are separate operations. Both distinctions are described by
[Ettus's X3x0 manual](https://files.ettus.com/manual/page_usrp_x3x0.html), which
also calls for power-cycling and probing after an FPGA update.

Required follow-up is a coherent chosen driver/image pair, correct executable
and Python-library selection, verified image provenance and an actual device
initialization check. A compatibility failure must not be treated as success
or indiscriminately trigger flashing on unrelated network failures. A setup
correction needs isolated before/after regressions and attached verification;
none has been implemented in this diagnostic pass. Restoring the current
Ethernet link, publishing reviewed commits, rerunning setup and final hardware
acceptance remain open. Do not flash an unidentified device or replace all
existing dirty cleanup work to bypass those boundaries.

### Authorized UHD 4.6 load attempt — 08:30 UTC

The user subsequently authorized loading the FPGA image matching UHD 4.6,
explicitly deferring changes to `setup.sh`. We reconnected to `nads2` and
checked the prerequisites without starting an image write. The read-only
commands and raw outputs are retained in
`audit-results/nads2-uhd46-flash.kNajmY/preflight.txt` (SHA-256
`21f03a837228ccbd0435ba40f9e7bd8a04764bcb4e388dfb8c40f34355b9ac19`).

- At 08:29:52 UTC `enP7s7` and `enx00e04c360e73` were both DOWN, unavailable
  in NetworkManager, and `ethtool` reported **Link detected: no**. The command
  also printed a netlink permission warning; the no-link result is corroborated
  by the other independent interface readbacks. `enP7s7` advertises 10 Gb/s;
  the USB adapter advertises only 10/100 Mb/s and is not a substitute for the
  intended USRP link. No interface settings were changed.
- Fixed-address UHD discovery and broadcast discovery each returned no
  devices, exit 1. The `--init-only` probe returned no devices, exit 255.
  The route to 192.168.40.2 still used the Wi-Fi default gateway. No process
  matching the inspected receiver/probe/image-loader patterns appeared in
  the initial process snapshot; this is not a global hardware-ownership proof.
- The selected `/usr/bin/uhd_image_loader` resolves to the UHD 4.6 library.
  Loader SHA-256 is
  `5beeec47d13d6118da8e769715625c0d2991a0fd430376a3fc1913757d2e3449`;
  library SHA-256 is
  `d0929c1df50ebc459573fdcdaba06c8a7037de157937e5c138859aa6098d9d13`.
  Image/inventory identities remain as recorded above. Fresh on-device model,
  serial/revision and image selection cannot be established without contact.
- No flash command was invoked, no package or network changes were made,
  and neither setup script was changed or run. Remote setup remains hash
  `b27d9fba28bb327cb29ac25100e1c0b4bfc1b46596120528119b01329a111e89`;
  local setup remains
  `9ee6fe8ee4b9a016272cd53295a42b058c5fb8a0b1cec9036697a86f0e47c851`.
  The requested load and post-load probe remain **not performed**, blocked by
  missing device connectivity. No completed-installation claim is justified.

The additive logging question was checked again against the retained
controller `open(..., "a")`, backend `reset_session_logs`, logger reopening
with `mode="w"`, run archiving, and installed launcher's `tee -a`. These have
different ownership and lifetime boundaries: a fresh backend run resets named
current logs but not the controller console, global launcher console or old
archives. The installed code lacks `logging_enabled`. The historical build's
execution host/time is still unestablished; installed files and a packaging
script on NADS 2 alone do not prove the build was performed there. No logging
settings or implementation were changed and no receiver was started.

### Reconnection and image identity — 08:34–08:38 UTC

The user reported that the USRP was powered, asked which image the receiver
requires, and reiterated that the logging question concerns anti-jamming
itself. The previous UHD 4.6 FPGA-load request remains the change scope;
`setup.sh` and logging changes remain deferred.

New read-only discovery identifies exactly X300 serial `35D068D` at
192.168.40.2, flavor HG. `enP7s7` has 192.168.40.1/24 and the route is now
direct Ethernet. We did not change the interface. Both normal and debug
UHD 4.6 probes reproduce the Radio-block mismatch and exit 255. The debug
probe gives the previously missing running-image identity:

```text
Using FPGA version: 39.3 git hash: d375d68
Radio#0: Expecting 0.1, actual: 1.0
```

The anti-jamming `.aj/bin/python` resolves `uhd` to
`/usr/lib/python3/dist-packages/uhd/__init__.py` and its process maps show
`/usr/lib/aarch64-linux-gnu/libuhd.so.4.6.0`. An initial attempt to call
`uhd.get_version_string()` failed because that Python attribute does not exist;
the loaded-library observation replaces it and the failed diagnostic is
retained, not hidden. A downloader dry run against the protected system image
directory refused write permissions before downloading; the actual preparation
uses a separate writable temporary directory, without changing system files.

Official UHD 4.6 image preparation:

```bash
/usr/bin/uhd_images_downloader --types x3xx_x300_fpga_default \
  --install-location /tmp/nads2-uhd46-images.YgMPGR --yes --test --keep
```

The downloaded archive hash is
`be555ccbc92ee4b03dee2ea5d0fca3c627ad585653a04c53d1e32cc492b15f33`,
equal to the official v4.6.0.0 manifest. Its HG bitfile hash is
`5786ab970c0219b27b5b361f2df7f7f1cf926b912eea420a1e701ae21b8cb4c2`;
`cmp` also confirms equality with the preexisting system HG file. Thus the
downloaded file's presence was not the original compatibility issue.

Before writing, the official loader was used in readback-only mode:

```bash
UHD_IMAGES_DIR=/tmp/nads2-uhd46-images.YgMPGR \
  /usr/bin/uhd_image_loader \
  --args type=x300,addr=192.168.40.2,serial=35D068D,fpga=HG \
  --download --no-fpga \
  --out-path /tmp/nads2-uhd46-images.YgMPGR/x300_35D068D_before_uhd46
```

The retained official `x300_image_loader_v4.6.cpp` confirms that `--download`
selects flash readback and `--no-fpga` prevents the later write. The loader's
printed `FPGA Image:` path in this readback command is its candidate input,
not an assertion that the USRP was already running UHD 4.6. Readback and the
requested write/activation have separate outcomes, recorded below when known.

For anti-jamming-owned logs specifically: the inspected headless
`_start_backend()` returns false while an existing backend is running;
its IPC response distinguishes `accepted=true` from `started=false`.
An actual new backend `run()` calls `reset_session_logs`, reopening the named
root log files with `mode="w"`. Stop archives them, and an unfinished prior
session is recovered before a later reset. Therefore new-run truncation and
growth of retained `logs/runs/` can both be true. No claim is made that every
unowned file in the log directory is reset or that a duplicate Start should
erase an active run. These are inspected code contracts, not a fresh live
Start/Stop validation, and no logs were deleted.

### Flash backup complete; UHD 4.6 write started — 08:41 UTC

Readback completed at 08:40:53 UTC, loader exit 0 with successful finalization.
The 15,878,042-byte backup was copied to the laptop evidence directory as
`x300_35D068D_before_uhd46.bit`. Remote and local SHA-256 agree:
`f5b90a1ec255f768907021d69b2abc9155d349133358a080870f5ab855ba38c2`.
This is the loader's flash-range readback, not a receiver IQ BIN. The prior
running image identity remains the separately observed `d375d68`.

The targeted receiver/probe/loader process check at 08:41:18 UTC found no
matching active owners. At 08:41:34 UTC the authorized write was started:

```bash
UHD_IMAGES_DIR=/tmp/nads2-uhd46-images.YgMPGR \
  /usr/bin/uhd_image_loader \
  --args type=x300,addr=192.168.40.2,serial=35D068D,fpga=HG,verify
```

The loader identifies the expected X300 and verified image path and explicitly
reports device-side verification enabled. No timeout or kill is wrapped around
the flash process. The `configure` argument was not supplied: storing the new
image and activating it are separate steps. `flash-write.txt` retains the raw
operation. At this entry the write is in progress; successful completion,
readback and post-power-cycle initialization must not be inferred in advance.

The write completed at **08:46:59 UTC**, exit **0**, with successful
finalization and the vendor's power-cycle instruction. At **08:47:12 UTC** a
separate `--download --no-fpga` readback began, using output basename
`/tmp/nads2-uhd46-images.YgMPGR/x300_35D068D_after_uhd46`. After readback, the
command compares exactly 11,443,736 bytes using `cmp -n 11443736` against the
official bitfile. The loader reads a larger fixed flash range; padding beyond
the source bitfile is not included in the image-equality claim. Raw output is
`flash-readback.txt`. Readback completion and activation remain pending at this
entry. No software-configure flag or device power control was invoked.

### Stored-image verification passed — 08:51 UTC

Readback finalized successfully at **08:51:08 UTC** and the complete command
returned **0**. `cmp -n 11443736` confirmed that **every one of the 11,443,736
official image bytes matches flash readback**. The larger readback file has
SHA-256 `e6507c11ab1af27ceedf77b3f5058465ba21e6c0c9f2d336f0a14c99c6804904`.
The image was not merely downloaded or accepted by an HG-name check: it was
written with device verification and read back separately. Comparison beyond
the official image length is not claimed because the loader reads additional
flash bytes, including padding/out-of-image contents.

The user was notified that it is safe to power-cycle the USRP. The host stays
on UHD 4.6 and `setup.sh` remains unchanged. Activation of the new image and
successful RFNoC initialization after restart remain a separate pending gate;
this record does not claim a working receiver, streaming or GNSS acceptance.

The after-write readback was copied to the laptop evidence directory; its
hash agrees with the remote hash above. At 08:51:47 UTC the remote checkout
remained clean, its setup hash was unchanged, and the selected probe still
reported UHD 4.6. Local setup SHA-256 is also unchanged. No commit or push was
made. Document whitespace checks produced no diagnostics.

At **08:52:04 UTC** a bounded debug initialization probe still read active
FPGA **39.3 / `d375d68`** and reproduced the Radio revision mismatch. The new
4.6 bitfile has been verified in flash, but this observation shows the old
image remains active before power-cycling. Output is retained in
`post-flash-active-image.txt`. Do not describe this operation as a verified
working UHD 4.6 receiver until a post-restart probe succeeds.

## Installed anti-jamming log lifecycle test — September 18

At the user's request, the installed logging implementation on NADS 2 was
tested at **2026-09-18T09:30:02Z**. This was a bounded helper-level test with
temporary output, not a live receiver run or TramiqSDR controller test.
No production log was truncated or deleted, and no service, radio, source,
configuration or logging-policy change was made.

The test imported the actual installed file:
`/home/qvise/.local/share/tramiqsdr/runtime/antijamming/src/antijamming/logging/setup.py`,
SHA-256 `e2da7117612f5efb5dc57ef34fe9ae524205bb147f992944951ff455e41f9756`.
This hash matched the earlier inspected snapshot and the post-test readback.
Python `-B` prevented creation of production bytecode-cache files. Command:

```bash
/usr/bin/python3 -B /tmp/antijamming-log-lifecycle.oeSvAt/check_log_lifecycle.py \
  --module /home/qvise/.local/share/tramiqsdr/runtime/antijamming/src/antijamming/logging/setup.py \
  --output /tmp/antijamming-log-lifecycle.oeSvAt/logs
```

The harness refused to reuse an existing output directory and exercised
all **16** entries of the installed `LOGGER_DEFS` through real logger calls.
Six checks passed:

1. Seeded old markers were present before reset (negative control).
2. First Start helper cleared all 16 current log files.
3. First Stop helper archived the exact current bytes and kept current files.
4. Second Start cleared current files without changing the first archive.
5. Second Stop preserved both finalized archives; current files contained
   only second-run markers, and the first archive was still byte-identical.
6. Reopening idle-service loggers did not clear the current files; receiver
   Start and merely creating the logger set are different boundaries.

The two synthetic archived runs held 4,061 payload bytes including manifests,
while current named logs held 955 bytes. These artificial sizes demonstrate
retention, not real receiver logging rates. GUI/headless call sites were
inspected earlier and share these helpers, but their full IPC, concurrency,
crash recovery, native GNSS logging and RF lifecycle were not executed here.

Fresh read-only storage inventory of the **existing installed anti-jamming**
log directory found 281,656 KiB total allocation (275.055 MiB) and 60 run
folders. A separate `du` of `runs/` reported 279,320 KiB (272.773 MiB).
Earlier rounded storage observations remain historical, not exact new values.
Three tracking files, totaling 117,488,317 payload bytes, have two names each:
one in a run folder and one under `tracking-state/`. Their device/inode pairs
are identical with link count two. The installed bridge explicitly creates
these compatibility names using `os.link`. Consequently, separately summing
`runs/` and all non-run paths would double-count those payloads. The whole-tree
`du` above counts each only once. Retained GNSS tracking evidence is therefore
part of this accumulation, not only the small current root log files.

The file inventory has 1,430 paths and 1,427 unique regular-file inodes:
285,108,709 unique logical bytes and 287,358,976 allocated regular-file bytes.
Whole-directory `du` also includes directory allocations. Largest retained
files include tracking observables of 68,824,026 and 48,596,592 bytes and
archived `analysis.log` files of 33,883,934 and 33,609,473 bytes. This explains
the measured NADS 2 storage; it does **not** establish NADS 1's reported eight
GB or a causal connection to GUI lag. No archive-pruning policy was added.

Retained evidence:
[`audit-results/nads2-log-lifecycle.Njs3AF/`](../../audit-results/nads2-log-lifecycle.Njs3AF/)
contains the harness, six-check stdout, copied temporary test logs and result,
fresh size/inode inventory, and the installed tracking-archive source excerpt.
The earlier read-only reconnect failure remains separately retained; it is not
treated as a successful measurement. Remote `rg` was unavailable, so the
tracking-source lookup used `grep` and a bounded `sed` excerpt. Source review
is targeted, not a claim of reading all historical log contents.

### Verification and limits

Final deployed software gate: **555 passed, 1 hardware test deselected**:

```bash
cd /home/qvise/antijamming
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .aj/bin/python -m pytest -q -m 'not usrp'
```

The 27 setup/runtime cases use actual shell functions and mocked external
commands. They cover target identity, successful compatibility, mismatch,
busy/unrelated errors, interruption, failed writes, pending activation and
recorded-prefix selection. They are not RF acceptance. Broad tests also cover
logging-off live receiver data with synthetic stdout/NMEA/monitor messages.

Native compilation completed with six jobs and the repository's existing
Release flags. No replay, antenna test, jammer-on test, phase calibration or
streaming acceptance was performed. No source cleanup claim is made for the
still-open malformed-covariance release-hold defect or calibration validation.

At 10:25:02 UTC, after the operator receiver/service had exited, the shipped
setup FPGA function began writing the identified X300 `35D068D` at
`192.168.40.2` with device-side verification. That programming operation must
not be interrupted. Completion/activation is recorded separately below when
observed; reaching this stage alone is not hardware readiness.

Raw evidence and harnesses:
`audit-results/nads2-cleanup-deploy.KqhZ7V/`. This includes source snapshots,
initial failures, per-commit checks, full gates, build/download logs, exact
binding/image/executable hashes, idle-service output and FPGA loader output.
GitHub was not pushed during this deployment; source was transferred via Git
bundles, and every new commit uses the requested Git identity and typed subject.

## Setup recovery after autoremove — September 18

Recorded at 2026-09-18T12:25:48Z. This entry supersedes the earlier deployment's
pending-initialization boundary only for the actual initialization observed
below; it does not certify RF streaming or migrate the entire dependency stack.

### Baseline and reproduced dependency boundary

NADS 2 is `qvise@192.168.3.127`, hostname `spark-8219`. Its user-selected
`/home/qvise/Desktop/qvise/antijamming` checkout is clean on
`cleanup/no-usrp-verification-20260831` at
`2ba73819dac528d9110d6c3badb968c8417bf8d5`. Laptop and NADS 2 source hashes match:

The 15:15:01–15:15:27 host-local `apt autoremove` record lists
`python3-ruamel.yaml` among its removals. The package was absent at diagnosis.
The bundled GNSS-SDR README does not list ruamel; this particular requirement
belongs to building UHD's Python utilities. The current setup package list
does not explicitly install it. No source declaration was repaired in this run.

### Authorized recovery and actual result

After the user explicitly requested running setup, installed the missing
dependency and ran the existing script as qvise, using sudo only for its
privileged steps:

```bash
sudo apt-get install -y python3-ruamel.yaml
cd /home/qvise/Desktop/qvise/antijamming
ANTIJAMMING_USRP_IFACE=enP7s7 \
  ANTIJAMMING_USRP_ADDR=192.168.40.2 ./setup.sh
```

Installed `python3-ruamel.yaml` 0.17.21-1 and
`python3-ruamel.yaml.clib` 0.2.8-1build1. UHD configuration then reported the
ruamel dependency satisfied and completed its build/install. The full setup
returned **0**, recorded independently from the log pipeline's exit status.
The script's actual compatibility check reported:

```text
[setup] X300 35D068D: active HG image initializes with the selected UHD.
```

This run refreshed the release-matched `gd375d68` image download but did not
flash the USRP, because its active HG image initialized successfully. Setup
preserved the active wired connection, disabled automatic activation of the
competing NetworkManager profile `de42e9a2-b8d5-31fa-8c1b-2bb91455379e`, applied
its 50,000,000-byte socket limits and updated existing user VOLK profiles.
The runtime address was already correct and was not edited.

Native GNSS-SDR compilation and the script's version/import/file checks
completed. The built executable is
`gnss-sdr/build-antijamming/src/main/gnss-sdr`, SHA-256
`9648a595ab6ae9f66c410a385a7454cb57aa9960d9118860b6c0355cf8788429`.
No application source change, commit or push occurred during recovery.

### Important remaining boundary: mixed UHD dependencies

The complete log retains a nonfatal missing-Abseil CMake warning, selection of
glog/gflags, and a nested-make jobserver warning. The profiler's update output
includes `no architectures to test`; it is not a new benchmark result.
Large streamed console returns were truncated for display; complete logs were
saved before display truncation and copied with matching SHA-256 values.
Review used targeted dependency, warning, build and completion sections, not a
claim that every log line or every receiver path was semantically verified.
No new unit-test suite, live GNSS replay, RF streaming or jammer test ran.

### Tramiq comparison and evidence locations

Complete run artifacts are retained at both:

- NADS 2: `/home/qvise/tramiqsdr-start-stop-evidence/20260918/setup-retry.R0kzIs/`.
- Laptop: `/home/u/tramiqsdr-start-stop-evidence/20260918/setup-retry.R0kzIs/`.

Matched artifact SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `dependency-install.log` | `3f3ee05c4e5b0c087c2c77811fe5b23538da806d424bd79f298f6b2ec7166af7` |
| `setup.log` | `9ee3b2ef12002f63caca03e54fa4f364ff307fd481693d69d6b05ebbdf7c12d5` |
| `result.txt` | `f2c10ea6bf013c1751ce88ee2f1d4acd0259fc135accc85c218b71d30ee689f8` |

### Acceptance and retained artifacts

Ruff, Vulture at confidence 90, separate-cache compileall and `git diff --check`
passed. Logs retain the failed attempts as well as subsequent passes; review
of build output was targeted, not a claim to have manually read every log line.
The GNU Radio configure/build rounds and GNSS candidate build logs remain in
the same evidence directory.

Production GNSS-SDR executable SHA-256:
`34fa7351a1b268d1f48e4e1da764d01d84aaad121348858f2012514b69f29cad`.
Candidate build SHA-256:
`ff656d352f8423332f9fca5f34afabcbff10eaa3dff474b19c1db0dc0d57ce34`.
These differ by build paths/configuration; neither establishes RF equivalence.

After matched-stack acceptance, apt removed exactly `libuhd-dev`,
`libuhd4.6.0t64`, `libgnuradio-uhd3.10.9t64`, `gnuradio`, `gnuradio-dev`,
`gr-fosphor`, `gr-limesdr`, `gr-osmosdr`, and `libgnuradio-osmosdr0.2.0t64`.
These are reinstallable packages; no global autoremove was used. Old unowned
`/usr/share/uhd/images` assets remain, but the selected downloader/runtime uses
the explicit source UHD image directory. No claim that every historical UHD
file anywhere on the machine was removed is made.

### Why Tramiq had worked

No Tramiq changes were made. No new RF streaming, long-duration stability,
PVT, jammer or non-ARM runtime claim follows from this installation work.
