# Realtime Local GUI

This repository runs the X300/TwinRX anti-jamming runtime through a local PyQt GUI.

## Product Commands

Run the normal realtime product:

```bash
./run_realtime.sh
```

Do not run the realtime operator GUI over SSH X11 forwarding. In this repo's
measured runs, the SSH-forwarded display appeared as `DISPLAY=localhost:10.0`
with an active `SSH_CONNECTION`, and Qt later reported a broken X11 connection
or left the GUI process stuck while GNSS-SDR continued/cleaned up separately.

For interactive operation, open a terminal in the machine's local desktop,
change to this repository root, and run:

```bash
./run_realtime.sh
```

For non-interactive receiver diagnostics over SSH, use the offscreen Qt path:

```bash
QT_QPA_PLATFORM=offscreen ./run_realtime.sh --auto-start --auto-stop-after-s 170 --quit-after-stop
```

Run phase calibration from the sibling repo:

```bash
../phase-calibration/run_calibration.sh
```

The launcher accepts diagnostic lifecycle flags such as `--auto-start`,
`--auto-stop-after-s`, and `--quit-after-stop`; it does not accept RF/hardware
tuning flags.

The launcher intentionally does not expose hardware tuning, array geometry, GNSS-SDR
disable flags, or low-level UHD transport flags. Those values are product runtime
profile inputs, not operator launch inputs.

## Runtime Profile

Product runtime values live in:

```text
configs/antijamming/x300_realtime.json
```

That file owns the X300/TwinRX spec values such as sample rate, center frequency,
gain, channel order, antenna map, TwinRX LO map, array spacing, frame sizes, and
GNSS-SDR runtime profile values.

`sample_rate` is the single authored receive-rate setting. Changing that one
number also sets the USRP RX bandwidth and fixed minimum rate. Do not add
separate `usrp_rx_bandwidth_hz` or `min_sample_rate` keys; the loader rejects
those duplicates.

Both launchers build from the one checked-in product profile. There is no
runtime-overlay interface. Bench geometry, external transmitter settings,
expected bearings, and their RF-budget calculations belong in dated audit
evidence, not live product configuration.

The GNSS-SDR digital input filter uses `Freq_Xlating_Fir_Filter` at zero IF and
decimation 1. It has a fixed 2.6 MHz physical passband and a 3.0 MHz stopband
start, leaving a 200 kHz edge-to-edge transition on each side. The passband
contains the 2.046 MHz GPS L1 C/A null-to-null main lobe plus 277 kHz of guard
on each side. Startup rejects sample rates at or below 3.0 MS/s because they
cannot represent the configured stopband below Nyquist.

The translating filter is rendered with `filter_type=lowpass`, `bw=1385000`,
and `tw=175000`. There is deliberately no `number_of_taps`: GNSS-SDR passes
`bw` and `tw` to GNU Radio's `firdes.low_pass`, which designs a Hamming-window
filter automatically. At 4 MS/s, GNU Radio's estimate is
`N = floor(A*Fs/(22*tw)) = floor(53*4e6/(22*175e3)) = 55` taps; 55 is already
odd. Evaluating those exact generated coefficients gives 0.44346 dB maximum
ripple through +/-1.3 MHz and -43.7022 dB maximum response from +/-1.5 MHz,
meeting the 0.5 dB / 40 dB limits. The resulting linear-phase group delay is
`(55-1)/(2*4e6) = 6.75 us`.

The USRP address is fixed to the known X300/HG Port-1 10GbE SFP path:

```json
"usrp_addr": "addr=192.168.40.2"
```

The host-side 10GbE profile currently uses `192.168.40.1/24` on `enP7s7`.
`setup.sh` can auto-detect the connected X300 across the known X3x0 subnets;
the GUI runtime still uses the concrete `usrp_addr` value in this JSON file.

## GNSS-SDR Path

The product path uses the app-rendered FIFO GNSS-SDR template:

```text
configs/gnss-sdr/fifo_gps_l1.conf.template
```

The anti-jamming backend owns the USRP. The fixed product path computes one
shared spatial output and fans it out through dynamically assigned per-channel
FIFOs with PRN-specific complex continuity scalars. The configured receiver has
ten GPS `1C` channels. These FIFO rows do not represent ten independent spatial
LCMV solutions. There is no configuration-disabled legacy single-FIFO product
mode. Direct-USRP and RTL-SDR GNSS-SDR configs are not part of the product runtime.

GNSS snapshot entries always include `constellation`, `prn`, and
`satellite_id`. Summary lists use constellation-qualified labels; the GUI does
not consume the removed GPS-only integer-list aliases.

In normal realtime mode, GNSS-SDR runs.

Calibration is not a realtime pipeline mode. The standalone
`../phase-calibration` repo owns phase calibration capture, SynthUSB control,
and sweep tooling. This realtime repo consumes the exported calibration artifact
checked in at:

```text
configs/calibration/x300_phase_offsets_added_hw_100khz.json
```

Regenerate that file in the calibration repo when the RF wiring or LO topology
changes, then copy the resulting JSON artifact into this repo.

## Tests

The launcher runs non-GUI smoke checks before opening the GUI. Full tests,
including PyQt GUI tests, are run explicitly:

```bash
./run_tests.sh unit
```

The launcher avoids running PyQt GUI tests during startup because Qt native
teardown can crash after tests pass on some display sessions.
