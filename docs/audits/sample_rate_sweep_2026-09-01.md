# Realtime sample-rate sweep — 2026-09-01

## Status and scope

This is the living run record for testing the fixed ten-source Shared-U1
product pipeline from 4 through 10 MS/s. It is updated after each software or
attached-hardware result rather than reconstructed only after the sweep.

Attached testing was paused by the operator after the bladeRF was physically
removed. Spark still saw the X300 at `192.168.40.2`, but `lsusb` contained no
bladeRF device and `bladeRF-cli` reported `No bladeRF device(s) available`.
Therefore no new 5–10 MS/s bladeRF/GNSS run was started. Those rows remain
pending rather than being replaced with ambient-input evidence.

The saved product profile remains at 4 MS/s. Rate variants are made only in a
temporary worktree by changing the single authored `sample_rate`; derived X300
bandwidth, minimum rate, GNSS-SDR internal rate, per-source input-filter rate,
and resampler rates must follow it. No short waveform file is generated: every
attached run reuses the retained 1,200-second, 50 MS/s SignalSim file.

There is no jammer in this sweep. A run may auto-arm LCMV after healthy PVT,
but without jammer-evidence activation it should remain on uniform fallback.
Such a run tests transport, tracking, PVT, and lifecycle behavior; it does not
test nulling, physical suppression, or array-manifold correctness.

## Evidence matrix

| Pipeline rate | Software derivation | Attached X300 transport | bladeRF RF / GNSS tracking | Current conclusion |
| --- | --- | --- | --- | --- |
| 4 MS/s | Rate/follower/render regression passed | 980.233 s, zero FIFO drops | Eight PRNs and repeated PVT at bladeRF 10 dB | Completed for the observed schedule |
| 5 MS/s | Rate/follower/render regression passed | 1,200 s, zero FIFO drops; ambient input | Pending matched RF run | Transport passed; RF/PVT not yet proved |
| 6 MS/s | Rate/follower/render regression passed | Pending | Pending | Software propagation only |
| 7 MS/s | Rate/follower/render regression passed | Pending | Pending | Software propagation only |
| 8 MS/s | Rate/follower/render regression passed | Pending | Pending | Software propagation only |
| 9 MS/s | Rate/follower/render regression passed | Pending | Pending | Software propagation only |
| 10 MS/s | Rate/follower/render regression passed | Pending | Pending | Software propagation only |

The attached 5–10 MS/s runs use the same bladeRF command and 10 dB software
gain as the settled 4 MS/s comparison. The X300 and GNSS-SDR rate change; the
50 MS/s transmitter waveform and RF tuning do not.

## Existing 4 and 5 MS/s evidence

The detailed RF and direct-file evidence is in
`docs/audits/bladerf_gain_main_control_offline_2026-09-01.md`.

- At 4 MS/s the cleanup branch ran for 980.233 seconds with zero FIFO drops,
  eight tracked PRNs, and repeated valid PVT. The bladeRF settled at 10 dB.
- At 5 MS/s an attached-X300 ambient transport soak ran for 1,200 seconds with
  zero overflow, timeout, clipping indicator, or FIFO drop. It did not transmit
  the retained waveform and therefore did not establish GNSS tracking or PVT.
- A direct-file receiver test decoded the first 120 seconds at GNSS-SDR's
  existing 4 MS/s internal rate. It did not exercise the 5–10 MS/s realtime
  pipeline.

## Chronological run log

### 2026-09-01T16:15+05:00 — software rate-propagation contract

- Added a parameterized product-profile regression for every integer rate from
  4 through 10 MS/s.
- All seven cases proved that the one authored rate becomes `sample_rate`,
  X300 `usrp_rx_bandwidth_hz`, and `min_sample_rate`; the rendered ten-source
  GNSS-SDR graph uses the same value for `internal_fs_sps`, every input-filter
  sampling frequency, and every pass-through resampler input/output rate.
- Focused result, including the existing dynamic 32-channel/5 MS/s renderer
  case: `8 passed, 106 deselected in 0.44s`.
- The first command used bare `pytest` and failed before test collection because
  no global executable is installed. Rerunning with the repository-owned
  `.aj/bin/pytest` produced the result above. This was an invocation/environment
  error, not a failing test case.
- This is configuration/rendering evidence only. It says nothing about X300,
  CPU/GPU capacity, FIFO scheduling, GNSS acquisition, or PVT at those rates.

### 2026-09-01T16:20+05:00 — attached sweep paused before first new run

- Spark was reachable and its cleanup branch remained at `2aaab236`; the
  retained waveform existed at exactly 240,000,000,000 bytes.
- `uhd_find_devices` found the X300 at `192.168.40.2`.
- The bladeRF was physically absent: no Nuand device appeared in `lsusb`, and
  `bladeRF-cli` reported that no bladeRF device was available.
- No temporary rate worktree was created, no profile was changed, no
  transmitter was started, and no 5–10 MS/s RF result was produced.
- A stale read-only diagnostic from the old `main` comparison was found blocked
  because its broad glob included named FIFO files. Exact PIDs 192447 and
  192413 were terminated and verified absent. This was a diagnostic-command
  bug after the prior run, not evidence that the anti-jamming runtime remained
  active; future log extraction must enumerate regular log files and exclude
  FIFOs.
- After the new rate-propagation regression and documentation changes, the
  full warnings-as-errors/development-mode laptop suite passed: `407 passed,
  1 skipped in 15.06s`. The skip is the explicitly opt-in physical-USRP pytest;
  no attached run is inferred from it.
- Configured Ruff, Vulture at 90% confidence, Python `compileall`, both product
  shell syntax checks, and `git diff --check` also passed.

## Untouched-`main` FIFO stall: open investigation

One matched 4 MS/s `main` run tracked eight PRNs and produced PVT before
zero-based FIFO source 9 stopped accepting data for the fixed 0.250-second
deadline. The cleanup run did not reproduce the failure during a longer
schedule.

This is not yet attributable to the cleanup changes:

- both revisions use the fair striped multi-FIFO writer;
- both use the same bounded FIFO write deadline;
- one failure and one non-failure cannot distinguish a deterministic fix from
  receiver scheduling, transient backpressure, or another timing condition;
- cleanup did change GNSS-SDR lifecycle ownership, process/log monitoring,
  startup rollback, and teardown behavior, but none is yet tied causally to
  source 9 becoming unread.

The exact observed failure remains:

```text
GNSS pipeline failed: GNSS-SDR FIFO reader stalled: sources=9
paths=.../gnss_iq_channel_09.fifo pending_bytes=16384 timeout_s=0.250
```

A fix claim requires either a reproducible old/new schedule connected to a
specific change or added instrumentation that identifies why the GNSS-SDR
reader stopped draining that source. The 5–10 MS/s sweep is useful stress
evidence but cannot by itself prove that root cause.

## Per-run recording rule

Each rate result is recorded here immediately with the exact commit/profile,
session path, elapsed time, X300 counters, FIFO totals/drops/latency/spread,
GNSS-SDR errors, PRNs/C/N0/PVT evidence, LCMV state transitions, and terminal
process/USRP/bladeRF ownership. A failed run remains in the record; it is not
silently replaced by a later retry.
