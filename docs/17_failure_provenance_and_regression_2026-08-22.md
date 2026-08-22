# Failure provenance and regression ledger — 2026-08-22

This ledger separates four observed failures that have different evidence and
must not be merged under the word "overflow".  A status is **fixed** only after
the regression criteria in its section pass in repeated cold full-Tramiq runs.

## Reproducibility rules

- Cold run: Tramiq **Stop**, wait for receiver and anti-jam children to exit,
  then Tramiq **Exit** and verify the GUI PID is gone before relaunching.
- Restart the selected bladeRF file at sample zero before starting the receiver.
- Record anti-jam Git branch, HEAD, dirty diff/hash, runtime configuration,
  TX host, bladeRF serial, file path/hash/size, gain, sample rate, RF bandwidth,
  and physical jammer/attenuator state.
- For `l1_static_50_1584.bin`, request Stop early enough that all evaluated
  receiver samples remain below 240 seconds.  Never use samples at or beyond
  the user-reported invalid region near 270 seconds.
- Inspect both anti-jam GNSS-SDR and the native Tramiq Receiver.  Record C/N0,
  tracked PRNs, PRNs used in PVT, PVT state, raw and output queue levels, FIFO
  write latency, USRP overflow/timeout counters, and RF input power.
- A jammer interval is real only when the physical switch state is supplied or
  a clear measured RF onset/offset is present.  A software latch alone is not
  proof of a physical jammer.

## 1. No-jammer periodic PRN/C/N0/PVT degradation

Status: **reproduced; not fixed**.

Exact full-Tramiq experimental control:

- Session: `20260822T114556.651622Z_pid194231`
- Source: clean `per-prn-fifo-experimental`
  `9a23c86455cde636b3afab2a930c792bdaaec525`
- TX: Evo X2 `192.168.3.116`, bladeRF serial ending `1039c`,
  `l1_static_50_1584.bin`, gain 66, 50 MS/s, 50 MHz, sample zero.
- Physical jammer: off; detector latch and protection remained false.

Anti-jam GNSS-SDR low-C/N0 intervals were receiver seconds 23–27, 33–37,
and 83–87.  During 83–87, raw RF power remained essentially unchanged and
queues remained empty.  PVT became false at receiver second 88 and recovered
at 101 after C/N0 had already recovered.  This difference is expected: PVT
also needs tracked channels, valid observables/navigation state, and enough
satellites used in the solution.

Native Tramiq independently showed L1 degradation and loss/reacquisition in
the same run.  Its exact timing differs because it is a separate antenna,
front end, receiver, and sample clock.

Direct read-only SC16/Q11 file checks at seconds 10, 22, 24, 26, 30, 32, 34,
36, 50, 80, 82, 84, 86, 100, and 150 found:

- total file power range: -32.293 to -32.278 dB (0.015 dB span);
- integrated 4 MHz GPS-L1 power range: -37.511 to -37.459 dB (0.052 dB span).

An independent software acquisition directly from the stored file at seconds
70, 81, and 90 acquired all nine selected PRNs.  Their correlation metrics and
Doppler bins remained stable.  One-megabyte source windows at seconds 0, 1, 70,
85, 86, 87, 88, 89, and 90 are byte-identical between this laptop and Evo.  The
complete 60 GB hashes differ, and a sample at second 269 differs, which is
outside this valid interval and is consistent with the separately reported
late-file problem.  Therefore the 85--89 second failure is not an encoded
power or acquisition-strength dropout in those stored bytes.

A second exact isolation used larger bladeRF TX buffers: 131072 samples,
24 buffers, and 12 transfers (about 12 MiB total and 63 ms of samples).  Session
`20260822T121910.671190Z_pid254813`, clean experimental `9a23c86455cd`, again
collapsed at mapped BIN offsets 85.39--89.41 seconds.  C/N0 fell from 49.38 to
29.00 dB-Hz and PVT was false for receiver seconds 48--60.  Raw/output queue
high-water marks were only 11/512 and 6/64, with zero rejection, USRP overflow,
or timeout.  Increasing the TX buffer therefore did not fix or move the fault.

The user reports that the same physical bladeRF radios, bladeRF-cli workflow,
repository, and valid BIN region play correctly from Strix and other hosts.
The radios are therefore a known-good control rather than the leading fault
candidate.  The remaining boundary is the Evo path after stored-byte read:
storage delivery, host scheduling, libbladeRF/USB transport, or device
streaming.  Disabling Evo Wi-Fi and changing process/CPU/I/O priority remain
controlled hypotheses, not fixes.  The buffered isolation did not produce
contemporaneous native Tramiq logs, so it proves GNSS-SDR behavior only.

A separate 120-second Evo copy of the same prefix changed the inode, physical
placement, and cache state without changing the samples.  Session
`20260822T123317.971903Z_pid260135` passed the formerly failing file offsets:
C/N0 was 49.06, 49.50, 49.13, 49.31, 49.38, and 49.25 dB-Hz at offsets 84.266
through 89.384 seconds.  PVT remained true after first fix, queues stayed below
13/512 and 1/64, and rejection/overflow/timeout counts were zero.  The copy was
not less fragmented: the original first 120 seconds occupy 190 extents and the
copy 212.  The pass therefore rules out sample content and simple extent count,
but does not yet distinguish cache warmth, physical placement, or inode-specific
read behavior.  Native Receiver telemetry was not produced in this run.

The post-reboot paired control on clean `main` `341953653f476680d7e20020a12153586b45c4f6`
removed cache warmth as the remaining explanation.  The Pocket 8-channel FX3
was verified on a 5-Gbit/s USB path, and Tramiq used the existing
`/home/qvise/tramiqsdr/conf/l1l5_10.conf` configuration in both cold runs.

- Warmed original inode, session `20260822T131358.803574Z_pid58844`: the first
  24 GiB was read successfully into page cache immediately before playback.
  Nevertheless, at mapped BIN offsets 85.32--92.32 seconds anti-jam GNSS C/N0
  fell `48.33 -> 33.23 -> 29.48 -> 29.00` dB-Hz.  PVT became false and recovered
  at mapped offset 103.32 seconds.  Raw RF and FIFO output power remained
  steady.  The raw queue remained empty with lifetime high-water 21/512,
  rejections 0, USRP overflows 0, and timeouts 0.  Native PocketSDR independently
  logged extensive loss/reacquisition near the same interval, then later
  recovered to a 20/21-satellite FIX.
- Byte-identical prefix-copy inode, session
  `20260822T131909.793834Z_pid61499`: anti-jam C/N0 stayed between 47.27 and
  47.68 dB-Hz at mapped offsets 85.39--92.39 seconds, with PVT true, nine
  tracked PRNs, seven used, raw queue 0/512, lifetime high-water 22/512,
  rejections 0, overflows 0, and timeouts 0.  This is the second copy-inode pass.
  Native PocketSDR produced one FIX row but later reported `PNTPOS ERROR, lack
  of valid sats`; therefore the full native path did not pass this control.

The original still fails after pre-reading while the byte-identical copy has
passed twice.  Cache warmth and simple extent count are now ruled out.  The
remaining Evo boundary is inode/physical-placement-sensitive or nondeterministic
playback after the read syscall (scheduler, libbladeRF/USB submission, or device
stream continuity).  More repetition and direct bladeRF TX-side continuity
instrumentation are required before choosing between those possibilities.

Host comparison found an important controlled variable without implicating the
physical radios.  Evo uses clean Nuand source/install commit `73ce750` and
reports libbladeRF `2.6.1-git-73ce750`; Strix uses clean commit `41b7fc7` and
reports `2.6.1-git-41b7fc7`.  The two commits between them revert TX gating and
recalibration protection around sample-rate and bandwidth updates.  They can
affect startup configuration, but do not naturally explain a repeatable fault
85 seconds into an unchanged stream.  Evo also uses kernel 6.8 and one shared
10-Gbit/s xHCI root for both 5-Gbit/s bladeRF devices, whereas Strix uses kernel
6.17 and exposes 20-Gbit/s xHCI roots.  Evo's two bladeRF USB devices both have
runtime power control forced `on`; autosuspend is therefore not the observed
cause.  Both hosts use `powersave` governor with `performance` energy preference,
128 KiB NVMe read-ahead, the `none` NVMe scheduler, and
`usbfs_memory_mb=16`.

Strix's exact bladeRF commit was built successfully on Evo in an isolated tree:
`/home/simulator/bladeRF-strix-41b7fc7/build-isolated/output`.  Its CLI reports
`1.10.0-git-41b7fc70` and links its matching isolated `libbladeRF.so.2`.
Installation intentionally did not overwrite `/usr/local`; CMake stopped when
it attempted to install unchanged udev rules without privilege.  The build-tree
binary remains usable for a one-variable playback comparison.  A passive
sidecar at `/home/simulator/tools/evo_bladerf_sidecar.sh` records process,
thread, disk, IRQ, VM, and optional serial-filtered usbmon evidence without
opening or configuring either radio.

Regression criterion:

1. At least three cold, sample-zero, jammer-off runs on the same exact source.
2. Evaluate receiver seconds 0–220 only.
3. No established PRN loses tracking; no PVT-true to PVT-false transition.
4. Established-channel C/N0 must not fall by more than 3 dB for more than one
   second unless a matching measured RF-input change is documented.
5. Zero USRP overflows/timeouts, zero rejected GNSS chunks, and no FIFO pause.
6. Native Receiver and anti-jam GNSS evidence are both archived.

## 2. GNSS-SDR handoff FIFO pause

Status: **root cause identified; two isolated fixes implemented and statically
verified; hardware regression pending**.

Exact failure session: `20260822T110412.583294Z_pid151377`, clean
`main`/`origin/main` `341953653f476680d7e20020a12153586b45c4f6`.

At 16:05:22 local time, a GNSS-SDR FIFO branch stopped consuming.  The handoff
thread was observed blocked in `pipe_write`; GNSS receiver time stopped while
the GNSS-SDR process stayed alive.  The producer remained near 122–123 chunks/s,
USRP overflows stayed zero, and the raw queue then filled
`0 -> 122 -> 244 -> 367 -> 490 -> 512`, causing one rejection and a handoff
pause.  This is downstream consumer backpressure, not USRP ingress overflow.

Two independent mechanisms were present.

First, the bridge wrote all FIFOs serially with blocking `os.write()`.  A single
stalled reader therefore blocked the only handoff worker indefinitely.  Commit
`d7cfdd8` on isolated branch `fifo-resilience-experimental-20260822` replaces
that behavior with a fair nonblocking poll/write loop.  A 250 ms deadline raises
`GnssFifoWriteStall` with the exact source, FIFO path, and pending byte count.
Real Linux-pipe tests cover a partially draining reader and a completely
stalled reader.

Second, commit `435945af4a174392fbeedfc0a3b09d4f7456bdb5` had configured
`GNSS-SDR.synchronize_signal_sources=true`.  In the deployed vendored
GNSS-SDR, this connects all ten source branches to one 20 ms
`gr::sync_decimator` sample counter.  One unscheduled branch can therefore stop
the receiver clock and every FIFO reader.  That precisely matches the observed
receiver-time freeze at second 66 followed by the Python queue fill.  The
Python producer already writes the same sample count to each FIFO, and each
tracking channel carries its own sample counter for observables alignment, so
global source-clock lockstep is unnecessary.  Isolated commit `ec5a5e9` renders
`GNSS-SDR.synchronize_signal_sources=false`, retaining all ten per-PRN sources
while returning the receiver clock to conditioner zero.

The focused suite passed 86/86 and the complete anti-jamming suite passed
284 tests with one skip.  These are not yet called a deployed fix: three cold
full-Tramiq hardware runs must still pass the criteria below.

Regression criterion:

1. A deterministic fault-injection test stalls one GNSS FIFO reader.
2. The bridge identifies the exact source/FIFO and records bounded latency.
3. No capture-thread rejection and no silent source desynchronization occurs.
4. Recovery or controlled GNSS-SDR restart is explicit and sample alignment is
   re-established before data resumes.
5. Three full cold hardware runs complete with zero rejection/pause.

## 3. Anti-jamming preservation failure

Status: **shared-main failure proven; experimental behavior is intermittent and
not fixed**.

Clean-main pad-50 session `20260822T112340.800981Z_pid175280` measured an RF
onset of about +14.7 dB relative to the pre-onset normalized input power.  This
is a relative receiver measurement, not dBm and not a hardcoded value.

The shared LCMV diagnostic reduced its uniform-versus-LCMV modeled output by
about 26 dB.  The downstream shared preservation bank then computed per-row
complex scales of approximately +19.0 to +22.7 dB to preserve its modeled old
responses.  Those scales came from logged old/new complex responses; they were
not fixed constants.  Actual FIFO output initially received only a few dB of
net reduction.  Protected mappings then fell from nine to four, two, and one;
C/N0, tracking, and PVT collapsed.

The experimental branch adds independently constrained per-PRN LCMV rows and
accounts for the 55-tap GNSS input FIR's 27-sample group delay.  Its targeted
unit/bridge/runtime tests currently pass (98 tests), and no-jammer live vector
concentration is much better than the prior wrong-epoch failure.

Session `20260822T115705.503543Z_pid238496` supplied three measured physical RF
pulses on the experimental branch.  The first two preserved PVT and recovered
C/N0 within about two seconds, with median modeled jammer suppression of 39.17
and 41.73 dB.  During the third pulse, C/N0 initially recovered and then fell
to 28.89 dB-Hz.  All nine established PRNs still had mapped measured-vector
rows, but continuously restarted transitions temporarily reduced modeled
jammer suppression to only 4.8 and 5.5 dB.  Thus passing algebraic continuity
residuals and total-output-power reduction did not prove a stable applied null.

Regression criterion:

1. No-jammer criteria in section 1 pass first.
2. Every established PRN has a fresh, correctly mapped, concentrated desired
   vector before onset; the exact row applied to each GNSS FIFO is logged.
3. During a bounded physical jammer pulse, every established PRN remains
   tracked, C/N0 does not drop more than 3 dB, and PVT stays true.
4. Report actual FIFO-output suppression, desired-response error, null
   residual, weight norm/noise gain, transition progress, and mapping state.
5. New/unmapped PRNs can acquire during protection without using an unrelated
   shared desired vector.

## 4. Native Tramiq SIGSEGV/SIGABRT/buffer-overflow abort

Status: **historical failure proven; isolated hardening implemented; live and
sanitizer regression pending**.

Apport records 117 `python/tramiq_sdr.py` native-process failures from
2026-08-15 through 2026-08-22: 64 SIGSEGV, 51 SIGABRT, and 2 SIGBUS.  The text
`*** buffer overflow detected ***` is a glibc fortified-native-code abort.  It
is distinct from PocketSDR's `sdev: buffer overflown` message and from the
anti-jam GNSS queue reaching capacity.

The loaded `libsdr.so` and `librtk.so` are ignored/unversioned binaries; the
Tramiq worktree and tracked static archives are also dirty.  Later crashes did
not retain usable backtraces.  Source audit found credible unsafe formatting
and shutdown/lifetime paths, but no exact source line may be called causal
without a captured native backtrace.

Today alone, Apport records include SIGABRT during a Stop sequence, SIGSEGV
after a USRP-not-found startup, and SIGABRT while the anti-jam child continued
normally.  These events had no anti-jam FIFO rejection or USRP overflow, which
separates the native parent-process crash from the GNSS handoff pause.  Source
inspection found two concrete regression targets: the window-close path closes
the native receiver without clearing its Python pointer and shutdown can call
the controller close path again through `atexit`; separately, the NMEA builder
appends several sentences into a fixed 4096-byte destination without passing
remaining capacity.  These explain timing/load sensitivity but remain
candidates until a sanitizer failure or native backtrace identifies the exact
instruction.

An isolated probe called the deployed RTKLIB NMEA writers with all 221 compiled
satellite slots marked visible.  RMC, GGA, GSA, and GSV totaled 3538 bytes,
leaving 558 bytes in the 4096-byte destination.  That maximum-visibility case
does not reproduce an overflow, so the NMEA buffer is no longer a leading cause
for the observed glibc abort.  The lifecycle/stale-native-pointer path remains
the stronger source-level candidate, still awaiting a captured crash frame.

The latest private remote `release/v1.0` was fetched without exposing the local
Desktop token.  Its one additional change replaces a latent RTKLIB `vsprintf`
with bounded `vsnprintf`; it was imported only into isolated branch
`reliability-experimental-20260822` as commit `3d552b3`.  Our following isolated
commit `df995e2` makes controller shutdown serialized and idempotent, clears
`rcv_body` before native close in both Stop and window-close paths, writes each
NMEA sentence independently into the 4096-byte buffer, and corrects three
native format-argument mismatches.  It does not alter the deployed dirty
TramiqSDR tree.

The isolated native build succeeded.  `-Werror=format` plus ASan/UBSan
compilation succeeded for the touched native translation units.  Ten focused
lifecycle/receiver-UI tests passed.  One older fusion CSV test still fails
because current code emits an RX1-only state where that test expects no state;
that unrelated stale expectation prevents claiming the entire Tramiq suite is
green.  No exact historical crash frame exists, so the hardened branch still
requires repeated live Stop/Exit and failure-path testing.

Commit `d7d549b` adds two native sanitizer regression executables.  A 64 KiB
input to exported `sdr_parse_nums()` now truncates safely to its 1024-byte local
buffer, respects `SDR_MAX_NPRN`, and accepts a null input.  A maximum-satellite
RTKLIB case surrounds the 4096-byte NMEA destination with a checked guard and
measured RMC/GGA/GSA/GSV sizes of 81/92/386/2907 bytes.  Both tests pass under
ASan and UBSan against the isolated native libraries.

Regression criterion:

1. Preserve current binaries and hashes, then make a separate debug-symbol and
   sanitizer build without overwriting deployment binaries.
2. Enable recoverable core capture for a bounded reproduction.
3. Exercise cold Start, normal Stop, Exit, USRP-not-found, and repeated runs.
4. Resolve the first captured native frame and add a focused reproducer/test.
5. Require repeated Stop/Exit cycles with no SIGSEGV, SIGABRT, SIGBUS, glibc
   buffer-overflow abort, stale child, or double-close.

## Current branch policy

- Keep `main` exactly at `origin/main` `341953653f476680d7e20020a12153586b45c4f6`.
- Keep deployed anti-jamming `main` unchanged.  FIFO work is isolated on
  `fifo-resilience-experimental-20260822` at commits `d7cfdd8` and `ec5a5e9`;
  per-PRN beamforming work remains a separate experimental concern.
- Keep the dirty deployed TramiqSDR tree unchanged.  Reliability work is
  isolated on `reliability-experimental-20260822` at commits `3d552b3`,
  `df995e2`, and `d7d549b`.
- Never call a fault fixed from a single non-reproduction.
