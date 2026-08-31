# Cleanup-branch Spark hardware verification — 2026-08-31

## Scope and identity

- Source commit: `061e12fd8244a978c041cb59c982c933246b7b74` on
  `cleanup/no-usrp-verification-20260831`.
- The commit was transferred to `/home/tramiq_sdr/antijamming` with a Git
  bundle, without a GitHub push. Laptop and Spark resolved to the same commit;
  both `main` references remained at
  `0672377aa6c3fa53a11e09747e0cbd300c815579`.
- Spark host: AArch64, 20 logical CPUs, X300/HG at `192.168.40.2`, four TwinRX
  channels, and one detected bladeRF 2.0.
- There was no controlled jammer. No result below is jammer-suppression or OTA
  null-depth evidence.

## Software and X300 contract gates

- Spark development-mode suite: `397 passed, 1 skipped`. The skip was the
  explicitly gated physical-USRP test.
- Spark did not have the optional `pytest-cov`, Ruff, or Vulture executables in
  its `.aj` environment. Laptop ran those gates on the identical source:
  coverage suite passed at 78%, and configured Ruff/Vulture passed.
- Enabling `RUN_USRP_TESTS=1` on Spark produced `1 passed`. It initialized the
  four-channel X300 configuration, received a validated two-dimensional
  `complex64` chunk, and stopped the device.
- A separate 16-chunk snapshot received 524,288 samples per channel with all
  16 metadata states equal to `ok`.

## Bounded end-to-end runtime

The first 30-second bounded run exposed host configuration drift: the runtime
requested a 50,000,000-byte UDP receive buffer, but Linux capped it at
33,554,432 bytes. Repository `setup.sh` already specifies 50,000,000 bytes for
`rmem_max`, `rmem_default`, `wmem_max`, and `wmem_default`. Those intended
values were applied with `sysctl` and verified before repeating the run. This
runtime change may not survive a reboot unless the host setup persists it.

The repeated offscreen run exercised GUI control, the headless service, the
four-channel X300 receive path, ten dynamic GNSS FIFO channels, the customized
GNSS-SDR executable, live monitors, the diagnostic sidecar, and normal stop.

- Archived session:
  `/home/tramiq_sdr/antijamming/logs/runs/20260831T174730.791293Z_pid17012`
- Measured active interval: 26.4 seconds.
- X300: 3,063 chunks; zero overflow, timeout, startup overflow, startup timeout,
  or clipping-suspected interval.
- GNSS FIFO: 3,063 writes, 8,029,470,720 total bytes across ten sources, zero
  drops, `source_byte_spread=0`, maximum source lead 4,096 samples, average
  write 1.09 ms, maximum write 17.29 ms.
- UHD buffer warnings after the `sysctl` correction: zero.
- `errors.log`: empty. No anti-jamming, GNSS-SDR, or sidecar process remained
  after shutdown.
- Audit: 215 snapshots, every LCMV state off, every inferred jammer state
  unknown, no PVT fix, and no jammer-only suppression result.
- Sampled steady load: GNSS-SDR used roughly 5–6 CPU cores, the Python backend
  roughly 0.7 core, total host utilization roughly 30–35%, low NVMe
  utilization, and no X300-interface error/drop increment. This short sample
  is a profiling observation, not a worst-case bound.

Selected retained hashes:

```text
3182702e5f1d5cab88f5bd8fb8122dc1ead15d4ed5a605f11ba2e1a0ea9d8086  app.log
fa636b9238a1e4bd47a2529bd79b60b4086306419e7aa78ae03a2bd062b90f75  gnss_sdr.log
c412a1b00d1b5f56542aadba473c2b3545e6ca945a59a627cdad497148a5a544  uhd_console.log
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  errors.log
be6f89ea5fd294538d1f9c91314cf02a14a82c8cc3156dff7b714955b51b51fd  runtime_evidence.jsonl
cc6a55bd4495c37ec6baa7c3b4302ebaab4f5b15b2d6a44d6bfc917f1e36600e  session_audit.json
bb5c5c9fd6801b26527b80a3ece41a918466a3513731b1caf8d6f49b7a8345a9  session_audit.txt
```

## bladeRF-to-four-input screen

The existing static 120-second SignalSim file was used only as a known nonzero
SC16 Q11 waveform. SignalSim source or generated files were not modified.

- File:
  `/home/tramiq_sdr/Bins/combined_static_common_120s_50MSps_fc1582p469_sc16q11.bin`
- bladeRF: TX1, center 1,582,469,000 Hz, 50 MS/s, 48 MHz bandwidth.
- X300 screen: center 1,575,420,000 Hz, 4 MS/s, 4 MHz bandwidth, all four
  channels, receiver gain 0 dB.
- bladeRF gain steps: actual −23 dB, 0 dB, and +10 dB. +10 dB was the retained
  direct-wire screen ceiling; neither transmitter nor receiver gain was raised
  beyond that boundary.
- Each bladeRF-on capture returned 16 `ok` chunks. TX was stopped and read back
  as `State: Idle`, `Last error: None` after every step.
- Matching TX-off raw channel powers were
  `[-67.80, -66.63, -68.48, -69.18] dBFS`. At bladeRF +10 dB they were
  `[-67.94, -66.65, -68.40, -69.14] dBFS`, a change of
  `[-0.14, -0.02, +0.08, +0.04] dB`.
- The transmitted file window was not zero: the first 5,000,000 complex
  samples ranged from −1554 to +1477 with zero rail or non-finite samples.

Therefore no controlled bladeRF signal rise was detected on the selected four
TwinRX inputs. This screen proves the tested low-gain device start/stop and
four-channel receive contracts, but it does **not** prove that the physical
bladeRF path reaches those inputs. A cable/splitter/port/path confirmation is
required before any higher-gain GNSS tracking or LCMV test.

## Unproven boundaries

- No controlled jammer, jammer transition, antenna geometry, or measured OTA
  null depth.
- No GNSS PVT or C/N0 result in the short ambient runs.
- No LCMV activation, measured-U1 preservation result, or phase-continuity
  receiver comparison.
- No exhaustive concurrency schedule and no long-duration thermal/transport
  soak.
- No positive bladeRF-to-X300 path proof; therefore no bladeRF-driven
  anti-jamming result.
