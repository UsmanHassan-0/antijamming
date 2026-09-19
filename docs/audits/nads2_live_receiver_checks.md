# Live receiver and TX-gain checks

## Scope and identity — September 18, 2026

This is a bounded wired RF diagnosis, not a general anti-jamming acceptance
report. The laptop and NADS 2 cleanup checkout were at `01bb0d7`; no product
source, receiver profile, calibration, FPGA or oscillator trim changed during
these runs. NADS 2 is `qvise@192.168.3.127`, X300 `35D068D` at
`192.168.40.2`. TramiqSDR was launched from its Desktop source/venv, not the
older installed bundle. Its working tree contains operator/colleague changes;
those were not edited by this task.

The user supplied this physical layout: two bladeRFs feed a three-way
splitter/combiner, then a two-way splitter; one branch feeds Pocket SDR and
the other a six-way splitter with four USRP channels and two terminated
outputs. This is user-reported wiring, not an independently measured loss
budget. No connector input power in dBm was established.

Strix is `u@192.168.3.157`, reached through NADS 2. The requested `strix` SSH
alias was added on the laptop. No credentials are recorded here. Strix's clock
was approximately 26 seconds ahead of NADS 2 during adjacent queries; exact
cross-machine RF-onset/TTFF must not be inferred by subtracting their UTC
timestamps without correcting that uncertainty.

### Receiver and transmitter provenance

| Item | Recorded identity |
| --- | --- |
| Runtime profile SHA-256 | `98824e6efffdba59c24f5724d6a2c92dfdfec0e69f189764111c8c5b9aaa99d6` |
| Production GNSS-SDR SHA-256 | `34fa7351a1b268d1f48e4e1da764d01d84aaad121348858f2012514b69f29cad` |
| `setup.sh` SHA-256, both checkouts | `6c0767a26326e524b0535f179e660aa55f92455da54a15ef010fe20c7e96689b` |
| Source bladeRF checkout | `/home/u/bladeRF`, `41b7fc705651404e2a180c477309cb2d29f4d69b`, clean |
| Actual TX executable | `/usr/local/bin/bladeRF-cli` |
| TX executable SHA-256 | `117ea765433e40ad0397805deae32165e420d1c50d00ee4d864e9369d8ba0d42` |
| Actual source-built libbladeRF SHA-256 | `7232efc3de3b51aee7cd7f45dcfa408cb75b7cf668f6306dc179f00120e8b10f` |

The installed and build-tree bladeRF libraries have identical hashes; CLI ELF
build IDs match. This was not an apt bladeRF selection. L1 device serial is
`0d360cd0df5943bdb0049b859d30c131`; L5 is
`a8df023479b1450ea8f9b8b28ef1039c`.

SkyForge's existing L1 script selects TX1, 1584 MHz, 50 MS/s, 50 MHz bandwidth,
gain 20 and `l1_static_50_1584.bin`. The file is 60,000,000,000 bytes: 300
seconds at that SC16 rate. Its L5 script selects 1190.91 MHz, 53 MS/s,
53 MHz bandwidth, gain 20 and `l5_static_53_1190dot91.bin`, 63,600,000,000
bytes, also 300 seconds. These are not the earlier 1200-second recordings.
No full-IQ hash/bit-integrity claim is made.

## Measured results

The production receiver remained GPS L1 C/A at 4 MS/s, RX gain 45, ten FIFO
slots, the existing complex-gain calibration and automatic protection policy.
Its executable and profile hashes were checked again after the gain change.
Telemetry was captured passively from its existing Unix socket without
enabling product logging or substituting a standalone receiver.

| Run | Observed result |
| --- | --- |
| Ordinary-user launcher, gain 20 | USRP initialization reached; GNSS startup failed with `Permission denied: 'tracking'`; returned to idle |
| Privileged launcher, gain 20 | Receiver times 1–176 s retained; zero PVT/observable UDP packets and no positive C/N0; only part overlapped TX |
| TramiqSDR, gain 20 | Retained receiver times 86–135 s: no valid C/N0, observables or PVT; chart empty |
| TramiqSDR, L1 gain 40 | Nine GPS PRNs with C/N0 45.13–47.83 dB-Hz; first observed PVT at receiver time 40 s; PVT through 134 s; 942 PVT packets, up to seven satellites used |
| Privileged launcher, L1 gain 40 | Nine GPS PRNs with C/N0 45.43–47.73 dB-Hz; first observed PVT at 27 s; PVT through 106 s; 792 PVT packets, up to seven satellites used |

GPS IDs with positive C/N0 in both gain-40 anti-jamming runs:
G03, G04, G07, G08, G09, G14, G16, G27 and G30. The recorded Anti-jam screenshot
shows the PRN bars. No UDP parse errors or digital clipping flags were
reported in these captured windows. That does not prove analog linearity.
Automatic LCMV arming after healthy PVT is recorded; no jammer was used,
so this does not validate jammer suppression or release.

Pocket SDR's gain-20 screenshot reports a fix using 13 satellites and a
displayed C/N0 range of 30.1–47.8 dB-Hz. These are C/N0 values, not RF input
power measurements. Its gain-40 hot-start screenshot reports 33.3–53.0
dB-Hz, but a different position/altitude (about 1311 m altitude versus about
103 m in the gain-20 screenshot). No Pocket accuracy or hot-start acceptance
is claimed; this discrepancy remains uninvestigated. Do not promote gain 40
as an optimal setting for both receivers based only on anti-jamming success.

### Gain comparison and limits

The authorized gain-40 diagnostic used the same source-built CLI and existing
SkyForge TX settings, changing only L1 gain 20 to 40. L5 remained 20. It used
a temporary script outside product repositories because SkyForge controls
were not visibly responding to attempted input. The CLI readback confirms
40, and the script restores 20 after the one-pass file ends. Both TX processes
exited zero at Strix 13:48:05 UTC; subsequent inspection found neither running.
The CLI also confirms the final L1 gain of 20. No flash/trim write occurred.

This strongly supports inadequate L1 signal level for the previous USRP path:
both anti-jamming entrypoints acquired and produced PVT at the higher setting
without a receiver/profile change. It is not a calibrated attenuation
measurement, an A/B/A power sweep, a proof of absolute position accuracy, or
a claim that every possible cause was isolated. Receiver reinitialization,
waveform start epoch and elapsed time are additional comparison boundaries.

Tramiq's actual Start/Stop callbacks were exercised. Its gain-20 Start was
observed after the application had been launched; the actor for that Start
was not retained. Gain-40 Start and both Stops were issued through its actual
buttons. Both backend/native process pairs exited after Stop. The launcher
gain-40 auto-stop and observer returned zero. The test-launched Tramiq GUI was
then closed through its Exit button; no owned receiver processes remained.

## Setup recheck and remaining issues

Two existing issues are not solved by these results:

- Root-owned generated `logs/gnss-sdr/runtime`/`glog` directories prevent
  the qvise-owned receiver from creating its `tracking` directory. Privileged
  tests bypass that permission boundary; they do not repair it. Directory
  timestamps/current ownership do not prove the exact historical creator.
  No ownership changes were made.
- Setup checks a hardcoded `x300_phase_offsets_100khz.json`, while the
  active profile uses `x300_phase_offsets_added_hw_100khz.json`. The active
  profile loads during these runs, but setup's existence check alone does
  not establish that the selected calibration matches current hardware.

Strix's reported desktop/input freeze also remains unresolved. SSH, X11
queries and screenshots responded; load was low and approximately 58 GiB
memory was available. Keyboard/touchpad were enabled, synthesized keys and
buttons were released, and no whole-system freeze was established. One
touchpad-jump warning was found; it is not proof of the reported full freeze.
No reboot, desktop restart or user-application kill was performed.

## Retained evidence

- Laptop: `/home/u/tramiqsdr-start-stop-evidence/20260918/live-pvt.ixZgMH/`.
- NADS 2: `/home/qvise/tramiqsdr-start-stop-evidence/20260918/live-pvt.tKGIJP/`.
- Strix TX: `/home/u/antijam-pvt-evidence-20260918/`.

Raw JSONL contains all retained IPC records; `summarize_telemetry.py` reads
every record rather than sampling the final snapshot. Summaries distinguish
positive C/N0, valid PVT flags and unique UDP packet counters from mere PRN
assignment. `comparison-summary.json` and `gui-gain40-summary.json` contain
the aggregates and lifecycle events. Screenshots preserve both tabs and gain
settings. The initial observer that connected before a socket existed failed;
its empty output is retained separately, and a distinct retry captured data.
The first visible launcher run ended before its observer attached; its exit
zero is not counted as receiver evidence. The large evidence copy was
restarted with compression, not discarded.
