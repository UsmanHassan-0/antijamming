# GNSS product pipeline and `main` control — 2026-09-01

## Scope and claim boundaries

This record answers three bounded questions:

1. Does the cleanup branch receive and decode the generated GPS L1 waveform
   through the attached RF path, four X300/TwinRX channels, Shared-U1 FIFO
   fanout, and GNSS-SDR?
2. Does untouched `main` behave the same under a matched temporary control?

There was no jammer. The cleanup RF run began with LCMV disabled, then
`lcmv_auto_arm_after_pvt` armed it at session elapsed 540.123 seconds after a
healthy PVT decision. It remained in `fallback` on `uniform_array_sum` because
the input-power and generalized-covariance jammer-evidence gates did not fire;
no covariance-null weights were applied. The matched `main` run also reached
the armed uniform-fallback state before its FIFO failure. These runs therefore
do not prove jammer suppression, physical null depth, OTA array-manifold
correctness, or the absence of other thread schedules and hardware faults.

## Product-input provenance

- File:
  `/home/tramiq_sdr/SignalSim/generated/signalsim_l1_20min_50MSps_fc1582p469_sc16q11.bin`
- Exact size: 240,000,000,000 bytes, equal to 1,200 seconds at 50 MS/s,
  interleaved signed-int16 complex IQ.
- File center: 1,582,469,000 Hz.
- GPS L1 C/A offset in the file: -7,049,000 Hz.
- Resulting GPS L1 RF: 1,582,469,000 - 7,049,000 = 1,575,420,000 Hz.
- X300 product profile: 1,575,420,000 Hz, 4 MS/s, gain 45 dB, ten GPS L1 C/A
  channels, ten acquisition channels, LCMV disabled, Shared-U1 fanout enabled.
- The external transmitter replayed that waveform from sample zero for both
  branch comparisons. Its device-specific command, gain sweep, oscillator
  calibration, and trim evidence are owned by the transmitter repository, not
  this anti-jamming repository.

## Cleanup-branch product-path run

- Cleanup commit at run time:
  `d7e484380d73af1faa14c4588457543e7de30d0c`; the primary Spark worktree was
  clean. The separate 5 MS/s worktree exercised the then-current laptop
  cleanup files copied onto the same commit, with only its sample-rate profile
  changed to 5 MS/s.
- Archived session:
  `/home/tramiq_sdr/antijamming/logs/runs/20260901T101113.530014Z_pid137305`
- Finalized duration: 980.233 seconds; stop was operator-requested after the
  comparison evidence was collected.
- FIFO: 119,185 writes, 312,436,326,400 bytes, zero drops, average write
  1.51 ms, maximum write 31.89 ms, 1,048,576-byte pipes,
  `source_byte_spread=0`, maximum source lead 4,096 samples.
- `errors.log`: empty.
- GNSS receiver log: 312 `internal_valid=1` PVT pipeline records and 319
  RTKLIB residual summaries. Replaying from sample zero at gain changes caused
  reacquisition/reset intervals, so these counts are evidence of repeated
  valid output, not one continuous receiver-time interval.
- LCMV state: initially off; auto-armed at session elapsed 540.123 seconds;
  thereafter `enabled=true`, `mode=fallback`, and
  `active_lcmv_weights_source=uniform_fallback`. No jammer-evidence activation
  or covariance-null weight application was observed.

At the selected external-source setting, all eight visible PRNs produced valid
symbol output. Their settled means were 43.248--43.406 dB-Hz, and GNSS-SDR
continued valid four- to five-satellite PVT during the sampled interval. This
is a receiver-side observation for this test chain, not a transmitter-power
claim.

## Untouched-`main` matched control

The control used a detached temporary worktree at unmodified `main` commit
`0672377aa6c3fa53a11e09747e0cbd300c815579`. `git diff` remained empty. The
same customized GNSS-SDR executable was exposed through one untracked temporary
`gnss-sdr/build-antijamming` symlink because `main` correctly refuses a
non-repo-local receiver binary. The first launch without that symlink did not
start GNSS-SDR and is excluded from the comparison.

The valid control used the same 4 MS/s product profile and restarted the
external waveform from sample zero under the same transmitter settings.

- Archived temporary session:
  `/tmp/antijamming-main-rf-compare/logs/runs/20260901T103231.977101Z_pid189238`
- Finalized duration: 178.967 seconds.
- Eight PRNs tracked at approximately the same level as the cleanup branch;
  the final 20,000-record sample had per-PRN means of 43.125–43.586 dB-Hz.
- Before failure, the receiver emitted 21 valid PVT-pipeline records and 22
  RTKLIB residual summaries, reaching seven used satellites.
- X300 terminal summary: 21,368 raw chunks, zero overflow, timeout, startup
  overflow, startup timeout, or clipping-suspected intervals.
- At 15:34:04 local, the tenth FIFO (zero-based source 9) stalled:

```text
GNSS pipeline failed: GNSS-SDR FIFO reader stalled: sources=9
paths=.../gnss_iq_channel_09.fifo pending_bytes=16384 timeout_s=0.250
```

- FIFO terminal summary: 10,885 writes, 28,534,374,400 bytes, one drop,
  average write 1.69 ms, maximum write 28.76 ms,
  `source_byte_spread=16384`, maximum source lead 4,096 samples.

This one matched run demonstrates that `main` can initially decode the signal
but reproduced a FIFO-stall failure that the longer cleanup run did not. It is
evidence for this schedule, not proof that the cleanup branch can never stall
or that one specific source change alone caused the difference.

## Separate 5 MS/s transport soak

A separate temporary cleanup worktree completed a 1,200-second attached-X300
transport soak at 5 MS/s before the bladeRF sweep. This was an ambient
transport/lifecycle test, not a GNSS waveform or jammer test.

- X300: 182,405 raw chunks; zero overflow, timeout, startup overflow, startup
  timeout, or clipping-suspected interval.
- Ten GNSS FIFOs: 182,404 writes, 478,161,141,760 bytes, zero drops, average
  write 1.15 ms, maximum write 30.58 ms, 1,048,576-byte pipes,
  `source_byte_spread=0`, maximum source lead 4,096 samples.
- `errors.log`: empty.

This proves the deployed ten-source transport under that 5 MS/s schedule. It
does not prove realtime capacity with 32 channels; the 32-channel regression
only proves rendered configuration and bounded pipe-write correctness.

## Excluded standalone receiver diagnostic

A separate direct-file GNSS-SDR diagnostic bypassed RF, X300 input,
anti-jamming processing, and FIFOs. It is not counted as anti-jamming
verification. Detailed receiver-only evidence belongs in the GNSS-SDR
repository; this record retains only the exclusion boundary.

## Selected hashes

```text
a242cbd5a9f29edc3b083ebe8f304372e729cf386d2697f9ee76218d19fc56e7  cleanup session_manifest.json
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  cleanup errors.log
55fc58aff2e273d3f4bfdc83c1b1801026acca8fbeace54aa3ad635b65c182c4  cleanup receiver.log
71094b003c7f8880844110cfb3d733e01d71e11f79cc5f4658152ca9a36f5054  main-control session_manifest.json
c5f857fe988d0bc10e12b5129ea4987a2394a6f5726632f5dcdede51cd807adb  main-control errors.log
aee288a0555723773b21676e746b8b457b4471b149623fe6d2d3a624340a63ee  main-control receiver.log
```

The cleanup archive remains under the primary Spark repository. The temporary
`main` worktree was removed after this record was written; its material
terminal evidence and hashes are retained above.
