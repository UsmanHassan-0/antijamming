# System UHD runtime and cleanup-history reconstruction

## Requested contract — 2026-09-19

Use distribution UHD throughout anti-jamming and its bundled native receiver.
Remove private-driver build/selection changes from cleanup history while
retaining unrelated DSP, configuration, lifecycle and FPGA-safety work.
Main and the experimental branch are not rewrite targets.

The repository remains a source build of customized GNSS-SDR. Its bundled
README lists `libuhd-dev` and `gnuradio-dev`; it does not mandate a particular
UHD release. `uhd-host` supplies image tools and probes, and `python3-uhd`
supplies the application's Python binding. Local Ubuntu package metadata lists
revision `4.6.0.0+ds1-5.1ubuntu0.24.04.1` for the UHD packages. The official
[Ubuntu 26.04 package](https://packages.ubuntu.com/resolute/uhd-host) is
`4.9.0.1-1ubuntu1`. The user's follow-up supersedes the initial strict-release
draft: install normal distribution packages rather than pinning one release.

## Implementation and limits

- Remove private UHD/GNU Radio builders and user-home runtime-prefix discovery.
- Install distribution UHD/GNU Radio packages together,
  clear native build searches, and reject incompatible dependency resolution.
- Retain exact-device discovery, real FPGA initialization, refusal to flash on
  busy/timeout/unidentified failures, verified programming and power-cycle gate.
- Retain unrelated runtime code, configuration, custom GNSS C++ and logging
  behavior. No receiver algorithm is changed by the dependency policy.
- No current hardware, native build, installation or RF acceptance is implied
  by synthetic setup tests. Remote production remains unchanged during testing.

Before reconstruction, the complete Git bundle and original documents were
retained outside the repository at
`/home/u/antijamming-uhd46-review.hofabn/`. Rewritten historical commits do not
inherit hardware acceptance from the archived versions. Original reports must
not be relabeled as runs of the new system-runtime tree.

## Verification record (in progress)

Tests execute on NADS 2 in isolated
`/home/qvise/antijamming-uhd46-check.uj4TqN/`, using its existing Python test
environment. They do not launch receivers, change packages or flash hardware.
An intermediate fixed-release draft passed 57 focused tests; a later no-env
draft passed 82. Neither count certifies the final distribution-selected policy.
Initial broad selection passed
578, failed one unavailable `gnuradio.filter` import, and deselected one USRP
test. These are intermediate results, not final acceptance.

Direct inspection of the downloaded Ubuntu ARM64 `uhd-host` package found
`/usr/bin/uhd_images_downloader`. An initial draft used an incorrect private
utility-directory assumption; its fake-tool test could not establish package
layout. The draft and test now use the package-proven command path. Download
on the laptop failed DNS; download and manifest inspection on NADS 2 succeeded.
No package was installed by that check. Subsequent results will be appended.

## Baseline hardware gate — 2026-09-19

The user requires NADS 2 acceptance before promoting the laptop candidate.
The initial real-system checks do not pass that gate:

- `/usr/bin/uhd_config_info --version` reports Ubuntu UHD
  `4.6.0.0+ds1-5.1ubuntu0.24.04.1`. The Python binding maps that same system
  library; a diagnostic attempt to call `uhd.get_version_string()` failed
  because that Python export is absent. Loaded-library maps, not that failed
  call, establish the binding's identity.
- Ethernet discovery identifies X300 `35D068D`, HG, at `192.168.40.2`.
  `/usr/bin/uhd_usrp_probe` exits 255: Radio revision 1 versus driver
  expectation 0. The accompanying socket-buffer warning is not the reported
  initialization exception.
- The existing repository and installed Tramiq GNSS binaries have identical
  SHA-256 `34fa7351a1b268d1f48e4e1da764d01d84aaad121348858f2012514b69f29cad`.
  Their ELF closure still selects the previous private runtime, not system
  UHD. `--version` succeeds, but does not establish system-driver or RF use.
- `libuhd-dev` was absent. Installed only that package, matching the existing
  system runtime, from APT's cached package. No package was removed. Normal
  source configuration now discovers system UHD and GNU Radio. A separate
  out-of-source build is in progress; it does not replace the production
  binary or modify GNSS source. Configuration alone is not build acceptance.
- The host could not resolve Ettus's download server. Download via the laptop
  passed the installed downloader manifest's archive SHA-256
  `be555ccbc92ee4b03dee2ea5d0fca3c627ad585653a04c53d1e32cc492b15f33`.
  Extracted HG bitstream SHA-256
  `5786ab970c0219b27b5b361f2df7f7f1cf926b912eea420a1e701ae21b8cb4c2`
  exactly matches the existing image on NADS 2. Verified image programming
  was started for that exact serial; completion and post-power-cycle probe
  are still pending. No new RF acceptance is claimed.

Raw commands/results are retained under the external preservation directory's
`baseline-20260919/`. The programming preflight also emitted a `pgrep` warning:
the full loader process name exceeds Linux's 15-character comm match. The
preceding process inventory found no active radio/build processes; that
particular long-name predicate is not counted as a successful guard. No
production guard was changed in this diagnostic.

### Native build result — 08:07 UTC

The isolated native build returned zero. SHA-256 of its executable is
`3c1e69df9c54608550bb6ebe72c237533df5eac82d76ce02b77e16fde9aa942e`.
The checker passes actual CMake cache, full ELF dependencies and adjacent
Python imports: system `libuhd.so.4.6.0` and system GNU Radio are selected.
`--version` returns `0.0.21`. Every originally tracked GNSS source file's
before/after SHA-256 matches; the production executable retains its original
hash. No source algorithm was changed and no production binary was replaced.
The same dependency checker rejects the old production executable with exit
1 because its GNU Radio dependency resolves into the old private prefix.
Checker SHA-256:
`b9d01f142a343128f1c4d9d41f73e8541be76e99181e99d5c23947395f4aff45`.

The build and validation commands, after successful fresh configuration with
system search paths and the existing UHD-enabled feature policy, were:

```sh
cmake --build /home/qvise/antijamming-uhd46-check.uj4TqN/baseline-build --parallel 8
/home/qvise/Desktop/qvise/antijamming/.aj/bin/python \
  /home/qvise/antijamming-uhd46-check.uj4TqN/verify_native_stack.py \
  --prefix /usr \
  --binary /home/qvise/antijamming-uhd46-check.uj4TqN/baseline-build/src/main/gnss-sdr \
  --cache /home/qvise/antijamming-uhd46-check.uj4TqN/baseline-build/CMakeCache.txt
```

Compiler capability probes reject unsupported ARM32/x86 flags on this ARM64
host. A nested Make jobserver warning falls back to one worker; the build
still finishes. Full build/configuration output is saved, although tool-view
output was truncated; warning/error searches and the affected capability
section were read separately. This is build/dependency/startup evidence, not
tracking/PVT or FPGA initialization acceptance.

Manual operator steps are saved outside the unaccepted cleanup branch in
`/home/u/antijamming-uhd46-review.hofabn/UHD-maintenance.md`, with a copy beside
the isolated NADS 2 build. They distinguish driver installation, image
download/programming/activation, receiver rebuild and dependency-aware removal.

### Verified programming completed — 08:09 UTC

The system UHD image loader completed all 87 sectors, reported successful
finalization and exited zero with device-side verification enabled. It
explicitly requested a USRP power cycle. The written image is the checksum-
verified HG image identified above. Activation and the post-cycle 4.6 probe
remain unverified until the operator cycles the USRP itself. The laptop
cleanup candidate remains uncommitted/unpromoted; production GNSS executables
and anti-jamming source remain unchanged. No tracking/PVT result is claimed.

### Post-cycle probe — 08:16 UTC

After the user reported FPGA 39.2, a fresh system-UHD probe completed with
exit zero on X300 `35D068D`. It reports FPGA version `39.2`, git hash
`6a990d9`, FW `6.1` and both TwinRX Rev C boards with four RX frontends.
The Radio compatibility exception is absent. The UDP send-buffer warning
remains; this successful initialization does not establish sustained sample
transport or tracking/PVT. The earlier exception was the Radio block's
compatibility revision, not a claim that the top-level FPGA number must
equal the UHD release number. Raw output: `baseline-20260919/postcycle-probe.log`.

At 08:17 UTC, `/usr/bin/uhd_images_downloader --help` exits zero and a fresh
DNS lookup for `files.ettus.com` succeeds. The earlier download failure was
an observed name-resolution failure, not a missing command. This later DNS
success alone does not prove a complete fresh download; no repeat download
or programming is needed for the already verified image.

## Tramiq stopped-state diagnosis and candidate tests — 2026-09-19

The user later supplied a downloader permissions error. That is distinct
from the earlier observed DNS failure: an ordinary user cannot write the
default `/usr/share/uhd/images` directory; their `sudo` invocation succeeds
and reports the computer's image inventory up to date. That does not report
or program the active image inside the USRP.

The production headless service log now reports the reverse Radio mismatch:
the explicitly selected private driver expects revision 1, while the newly
active image supplies revision 0. The controller invokes
`python -m antijamming.app.headless`; that module's existing bootstrap then
selects the private runtime through `app/uhd_runtime.py` and
`tools/uhd_runtime_env.sh`. This is a concrete application startup mismatch,
not evidence that the successful system-driver probe is wrong or that
downloading images failed. Production source and receiver selection have not
yet been migrated in this task.

Fresh candidate tests on the isolated NADS 2 copy:

- First focused/full attempts fail the documentation-layout test because two
  diagnostic documents had been copied to the staging root. These were test
  staging artifacts, not files in the product source. Preserve the failures.
- Move those two artifacts into the staging evidence directory; do not change
  product code or weaken the test. Repeat focused selection: **113 passed**.
- Repeat full hardware-free selection: **566 passed, 1 USRP test deselected**.
  These results do not establish actual controller/USRP/GNSS lifecycle success.
- A bounded real headless Start/Stop harness was prepared but not executed:
  a separately launched `uhd_image_loader` process was observed on NADS 2.
  Do not interfere with concurrent programming or count the unexecuted test
  as a pass. Candidate deployment and history rewrite remain withheld.

Raw logs, source selections and the diagnostic harness are retained under
`/home/u/antijamming-uhd46-review.hofabn/startup-20260919/`. No Tramiq GUI source,
operator changes, receiver settings or production binary were changed by this
diagnosis. The initial combined log/process tool view truncated its output;
the full output was retained and the startup-selection evidence was re-read
separately. The whole large Tramiq log was not semantically reviewed here.

### Candidate headless lifecycle and operator recheck — 08:36 UTC

After the separately launched image loader exited, the isolated candidate's
real headless Start/Stop completed without forced cleanup. The retained summary
reports service exit 0, 318 metrics, four state events, seven status events and
three replies. Its process maps select `/usr/lib/aarch64-linux-gnu/libuhd.so.4.6.0`.
Evidence: `startup-20260919/headless-check-eaxzy7lp/`. This is bounded candidate
lifecycle evidence, not production deployment or a tracking/PVT acceptance claim.

The user subsequently reported an RFNoC error from the production GUI launcher
despite programming and power-cycling the USRP. A fresh 08:36 UTC system probe
returns 0 and reports X300 `35D068D`, FPGA 39.2, hash `6a990d9`, and both TwinRX
boards. No additional image was programmed during this check. The socket-buffer
warning remains but does not prevent this initialization.

Production `run_realtime.sh` still sources `tools/uhd_runtime_env.sh` and calls
`antijamming_activate_uhd_runtime`. The GUI module also retains the private
bootstrap. `.uhd-runtime-prefix` names the old private installation. The retained
headless log selects that driver and reports Radio revision expected 1, received
0. Thus successful system initialization and the application's failure use
different drivers; the operator's image write is not evidence of a failed flash.
Production migration, full GUI/controller verification and history reconstruction
remain pending. This diagnostic did not change production files or services.

Raw evidence: `startup-20260919/operator-launcher-recheck.log` and
`startup-20260919/operator-probe-recheck.log`, under the external preservation
directory named above. The launcher review was targeted, not a complete reread
of every script or historical log.

### Production migration started — 08:40 UTC

The user explicitly requested completing the repair in the actual Desktop
checkout. Before transfer, all affected production source files matched the
recorded commit; existing unrelated documentation changes were preserved.
Transferred the candidate launchers, direct entrypoints, setup, dependency
checker and corresponding tests. Removed the three private build/environment
helpers, Python bootstrap, two superseded test modules and runtime-prefix
metadata from that checkout. Tracked originals remain recoverable from Git;
the metadata's previous value is retained in the diagnostic evidence.

An independent existing startup defect was also present: generated `logs/`
directories and files were root-owned. With no receiver/programmer running,
changed only root-owned entries beneath this exact checkout's `logs/` to
`qvise:qvise`, without following symlinks or crossing filesystems. No contents
were deleted and no general permission relaxation was made. Repeated privileged
external launches remain an ownership boundary requiring verification.

The first actual `./setup.sh` returned 100 during APT update because the unrelated
Cursor repository could not be reached over the attempted IPv6 connections.
No native rebuild occurred in that attempt. An IPv4-only APT retry succeeded;
the normal setup is being retried unchanged. No repository was disabled or
deleted and no persistent IPv4 override was installed. Raw outputs are
`production-setup.log`, `apt-ipv4-update.log` and `production-setup-retry.log`
under `startup-20260919/`. Completion remains unclaimed until the rebuild and
production lifecycle checks finish.

### Real reconfiguration exposes a stale optional-header cache

The unchanged normal setup retry passed APT, Python dependencies, system-UHD
imports, a fresh matching-image download and the actual FPGA initialization
gate. It did not flash the device. CMake then failed in the vendored
`FindGRLIMESDR.cmake` at reads of `/usr/include/limesdr/source.h`.
The production cache retained `GRLIMESDR_INCLUDE_DIR=/usr/include` and an old
library selection although that header no longer existed. The independent
fresh baseline build instead marked the optional dependency not found.
`ENABLE_LIMESDR=OFF` does not skip the vendored unconditional discovery call.

Correction: invalidate `*GRLIMESDR*` searches alongside UHD/GNU Radio during
setup reconfiguration. No GNSS algorithm or vendored finder was changed.
The new regression invokes that real finder with a missing cached header,
requires the before-case to fail, then requires clearing the cache to succeed.
The actual setup command-argument test also requires the new invalidation.
Remote focused tests: 45 passed across native setup, dependency validation and
FPGA setup tests. Full normal production setup is rerunning; raw output is
`startup-20260919/production-setup-cache-repaired.log`.

### Production setup and GUI boundary passed — 08:50 UTC

The complete normal `./setup.sh` finishes with status 0. System packages remain
the distribution's installed release; no explicit release constraint is in the
APT request or checker. The build invalidates the stale optional-header result,
finishes compilation, profiles kernels and passes actual CMake, ELF closure and
Python-import checks. The production GNSS executable SHA-256 is now
`74907b67ef22ab5724f2647704133fdcf1b0f41c9fa3ba46b8595443731c1f2e`.
No vendored receiver algorithm changed. The active HG image passed initialization;
setup did not invoke programming. Its existing socket-buffer setup also applied.

The actual Desktop `run_realtime.sh --auto-start --auto-stop-after-s 40
--quit-after-stop` completes with exit 0, no forced cleanup, no IPC errors and
356 metric events. States include `USRP stream started` and `USRP stream stopped`.
The observer records the GUI, headless process and native GNSS executable; loaded
maps use system UHD and GNU Radio. `QT_QPA_PLATFORM=offscreen` was set only for
this diagnostic so it did not take desktop focus. This exercises the shipped
GUI/IPC/radio lifecycle, not physical display rendering or established PRNs/PVT.
Evidence: `production-gui-hogwijbg/` and the corresponding observer-corrected log.

Two diagnostic defects are retained separately from product results:

- The first GUI observer searched only the main thread's child list and missed
  GNSS-SDR, which is spawned by a worker thread. That run had exit 0, 358 metrics
  and no IPC errors, but its strict observer returned failure. The revised
  observer visits all task child lists and the repeated run includes GNSS maps.
- The first controller observer tried serializing projected NumPy arrays
  directly; it aborted and closed its owned service. Added explicit NumPy
  conversion in the diagnostic only. The controller run is being repeated.

Before the final diagnostic-only test additions, the real Desktop test suite
passed 567 tests with one USRP test deselected. The version-selection tests now
also exercise simulated different releases and ARM64/x86 library directories;
23 focused verification tests pass. These fixtures establish release-independent
selection logic, not future-release hardware support.

### Sudo launch inspection — no Tramiq policy change

The user requested inspection only of the colleagues' sudo launch. Tramiq's
controller uses ordinary `subprocess.Popen` without a user-ID change, so a root
parent launches a root headless backend. This can leave root-owned generated
files that a later qvise run cannot overwrite. The exact historical creator of
the previously observed root-owned folders is not established from ownership
alone.

The connected FX3 `04b4:00f1` is `/dev/bus/usb/006/002`, mode 0666. The installed
`64-limesuite.rules` contains that permission rule. A read/write device-node open
and immediate close succeeded as UID 1000 with no USB transfers or interface
claims. Thus USB node permissions do not currently require sudo on this host.
The source controller and anti-jamming launcher run as qvise in these checks;
the full Pocket receiver and every Tramiq feature have not been exercised as an
unprivileged user. No launch policy, udev rule or Tramiq source was changed.

The separately installed portable Tramiq bundle retains its own old private
driver and wrapper. The Desktop checkout checks above do not certify that
separate installation. Its packaged dependency migration remains a distinct
open boundary; no automatic replacement of the colleague's Tramiq edits occurred.

### Repeated production controller and final software gates

The unchanged source `TramiqAntijammingController` resolves the actual Desktop
checkout and passes two sequential 40-second Start/Stop cycles as qvise.
Both cycles report stream readiness, record the real GNSS process and system
UHD maps, and finish with `stop_complete=True`. Fresh telemetry polls number
178 and 176. Final controller close succeeds; the diagnostic exits 0.
Raw evidence: `production-controller-mtwblkb2/` and
`production-controller-observer-corrected.log`. No Pocket stream, transmitter
or jammer was started by these tests. They exercise the actual controller API,
not a manual click-through of the entire Tramiq GUI.

Removed one remaining obsolete UHD-activation stub from the launcher test
fixture. Focused final selection: 76 passed. Final hardware-free suite on the
Desktop checkout: 576 passed, one USRP-marked test deselected. Ruff and Vulture
checks return 0, and `git diff --check` passes. Real radio lifecycle evidence is
the separately retained GUI/controller runs, not the deselected test count.
Root ownership has not reappeared in the inspected runtime directories after
these qvise runs. No tracked GNSS-SDR source or runtime configuration changed.

The cleanup-history reconstruction is now being prepared in separate Git
objects. No branch ref or remote has yet been rewritten; its checks exposed an
auditor bug returning the original hardware document after transforming it.
Corrected that reconstruction-only bug before promotion. The working Desktop
startup repair is independent of this still-pending history operation.

### Final cleanup-history preparation — 10:55 UTC

The later Desktop source includes a colleague's runtime-directory permission
recovery, which is retained with its modified regression rather than silently
overwritten. The release-hold correction is separately committed and passes
18 focused cases plus all 594 hardware-free tests on that actual checkout
(one USRP test deselected). These do not establish physical jammer-off PVT
recovery. Exact release evidence is linked from the jammer audit.

The rewritten suffix removes four private-driver-only commits and retains
the remaining mixed changes with product-oriented Conventional Commit subjects.
Machine identity stays in measurement provenance, not product commit names.
Original objects and dirty patches remain in the external preservation path
above. Main and the experimental branch are explicitly unchanged.

A new native check initially used the wrong diagnostic path
`gnss-sdr/build/CMakeCache.txt`; it failed because that path does not exist.
The actual setup defines `gnss-sdr/build-antijamming`. This is a diagnostic
path mistake, not a receiver or dependency failure; the corrected native check
and exact reconstructed-tree verification are pending at this checkpoint.

### Reconstructed-source and native verification result

The corrected actual-checkout command passed:

```sh
.aj/bin/python tools/verify_native_stack.py --prefix /usr \
  --binary gnss-sdr/build-antijamming/src/main/gnss-sdr \
  --cache gnss-sdr/build-antijamming/CMakeCache.txt
```

It selects `/usr/lib/aarch64-linux-gnu/libuhd.so.4.6.0` with system GNU Radio;
Python imports and loaded-library maps agree. No rebuild or hardware action
was performed by this check. Raw output is `deployed-native.log` beneath
`/home/qvise/antijamming-release-check.EqM2f2/`.

The reconstructed source archive also passes 594 tests, one USRP test
deselected, using `python -m pytest -q -m 'not usrp'`. The actual checkout had
already passed the same selection. The rewritten suffix has 18 commits, all
with Conventional Commit subjects and the requested author/committer identity;
private-driver-only commits are removed while mixed later corrections remain.
No claim of testing every historical commit or all runtime schedules follows.
The reconstructed source repeats all 594 passes with `PYTHONDEVMODE=1` and
`PYTHONWARNINGS=error`; Ruff, Vulture (90 percent confidence) and Bash syntax
checks also pass. Logs are `history-final-tests.log` and
`history-final-warnings.log` in the same remote evidence directory.

Original history is recoverable from the external bundles and the old/new
mapping, not an extra product branch. Historical machine names remain only
where needed to identify observations, not as a requirement in commit subjects.
Main, experimental and Tramiq packaging are outside this history operation.
