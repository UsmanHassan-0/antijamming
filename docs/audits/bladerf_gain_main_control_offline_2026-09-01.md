# bladeRF gain, `main` control, and offline SignalSim validation — 2026-09-01

## Scope and claim boundaries

This record answers three bounded questions:

1. Does the cleanup branch receive and decode the generated GPS L1 waveform
   through bladeRF, the confirmed splitter path, four X300/TwinRX channels,
   Shared-U1 FIFO fanout, and GNSS-SDR?
2. Does untouched `main` behave the same under a matched temporary control?
3. Does GNSS-SDR decode the waveform directly when the RF and anti-jamming
   paths are bypassed?

There was no jammer. LCMV was disabled. These runs do not prove jammer
suppression, physical null depth, OTA array-manifold correctness, or the
absence of other thread schedules and hardware faults. The direct-file run is
waveform/receiver evidence only; it does not exercise anti-jamming code.

## Waveform and tuning provenance

- File:
  `/home/tramiq_sdr/SignalSim/generated/signalsim_l1_20min_50MSps_fc1582p469_sc16q11.bin`
- Exact size: 240,000,000,000 bytes, equal to 1,200 seconds at 50 MS/s,
  interleaved signed-int16 complex IQ.
- File center: 1,582,469,000 Hz.
- GPS L1 C/A offset in the file: -7,049,000 Hz.
- Resulting GPS L1 RF: 1,582,469,000 - 7,049,000 = 1,575,420,000 Hz.
- X300 product profile: 1,575,420,000 Hz, 4 MS/s, gain 45 dB, ten GPS L1 C/A
  channels, ten acquisition channels, LCMV disabled, Shared-U1 fanout enabled.
- The SignalSim file was generated transactionally on Spark from the checked-in
  1,200-second full-L1 configuration. Its generation log reported 141.556 s
  end-to-end, 1,616.90 MiB/s, and zero clipped samples.

The bladeRF command was held constant except for the requested gain sweep:

```text
bladeRF-cli -e 'set frequency tx1 1582469000;
set samplerate tx1 50000000;
set bandwidth tx1 48000000;
set gain tx1 <GAIN>;
tx config file=.../signalsim_l1_20min_50MSps_fc1582p469_sc16q11.bin
  format=bin repeat=0 samples=131072 buffers=24 xfers=12 timeout=5000;
tx start; tx config; tx wait'
```

`repeat=0` is the bladeRF continuous-repeat setting. Each gain change stopped
the prior process and restarted the file at sample zero. Consequently,
transition intervals include receiver reacquisition and are not used as
steady-state C/N0 estimates.

## Cleanup-branch RF run

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

Observed C/N0, with the physical splitter path reconnected:

| bladeRF gain | Bounded observation |
| --- | --- |
| 0 dB | Initially no complete multi-PRN solution; G28 later produced valid symbol output around 28.4–37.8 dB-Hz, commonly about 35–36 dB-Hz. |
| 20 dB | All eight visible PRNs decoded; a settled interval gave per-PRN means from 48.927 to 49.281 dB-Hz and repeated PVT. |
| 15 dB | A pre-stop settled capture gave per-PRN means from 46.613 to 46.890 dB-Hz; all eight had valid symbol output. |
| 10 dB | A settled interval gave per-PRN means from 43.248 to 43.406 dB-Hz; all eight had valid symbol output and GNSS-SDR continued valid four- to five-satellite PVT during the sampled interval. |

Ten dB is therefore the preferred setting among this sweep for a strong but
less extreme receiver level. This is an empirical setting for this exact
cable/splitter/receiver chain, not a calibrated bladeRF RF-output-power claim.

## Untouched-`main` matched control

The control used a detached temporary worktree at unmodified `main` commit
`0672377aa6c3fa53a11e09747e0cbd300c815579`. `git diff` remained empty. The
same customized GNSS-SDR executable was exposed through one untracked temporary
`gnss-sdr/build-antijamming` symlink because `main` correctly refuses a
non-repo-local receiver binary. The first launch without that symlink did not
start GNSS-SDR and is excluded from the comparison.

The valid control used the same 4 MS/s product profile and restarted bladeRF
from sample zero at 10 dB.

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

## Direct-file GNSS-SDR run

The RF transmitter was off and no anti-jamming, USRP, or FIFO process was
running. The temporary config read the first 12,000,000,000 scalar `ishort`
items, equal to 120 seconds of the retained 1,200-second file. It used the
customized CUDA GNSS-SDR receiver, the correct -7,049,000 Hz translation, the
existing 50-to-5-to-4 MS/s conditioner, no throttle, and PRNs
2, 3, 8, 10, 21, 28, 31, and 32.

- GNSS-SDR wall time: 62.035 seconds for 120 receiver seconds.
- All eight intended PRNs produced navigation/ephemeris messages.
- Navigation-message C/N0 samples: 47 parseable records, 49.00 dB-Hz minimum,
  50.84 dB-Hz mean, and 52.17 dB-Hz maximum.
- First fix: 37.3527 deg latitude, -121.916 deg longitude, 96.5715 m height,
  GDOP 2.29605.
- Last printed fix: 37.352721 deg latitude, -121.915774 deg longitude,
  96.78 m height, using eight observations.
- 86 valid PVT-pipeline records and 171 printed position records.
- Receiver time reached 120 seconds and GNSS-SDR exited normally at EOF.
- No `ERROR`, `FATAL`, segmentation, or timeout line was present.

This proves the waveform and direct receiver configuration decode through the
tested 120-second prefix. It does not prove every sample or ephemeris cutover
in the remaining 1,080 seconds.

## Selected hashes

```text
a242cbd5a9f29edc3b083ebe8f304372e729cf386d2697f9ee76218d19fc56e7  cleanup session_manifest.json
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  cleanup errors.log
55fc58aff2e273d3f4bfdc83c1b1801026acca8fbeace54aa3ad635b65c182c4  cleanup receiver.log
71094b003c7f8880844110cfb3d733e01d71e11f79cc5f4658152ca9a36f5054  main-control session_manifest.json
c5f857fe988d0bc10e12b5129ea4987a2394a6f5726632f5dcdede51cd807adb  main-control errors.log
aee288a0555723773b21676e746b8b457b4471b149623fe6d2d3a624340a63ee  main-control receiver.log
ed5d18d436ec2deec96d15e4512ad44dced04be2d6578a047eca02b5f3790a9e  offline receiver.conf
873a0fea155b72950cde655376c5fe06c8b948bf37a194be32c249e6f247943b  offline console.log
```

The cleanup archive remains under the primary Spark repository. The temporary
`main` worktree and direct-file directory are removed after this record is
written; their material terminal evidence and hashes are retained above.
