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

For interactive operation from a laptop, use the configured GNOME RDP desktop:

```text
Protocol: RDP
Server: 10.189.184.209:3389
Username: qvise
```

The current workstation RDP session is configured as `antijam-gnome-safe`, which
forces GNOME Shell and the RDP handover path to use software rendering. This is
intentional: measured RDP login attempts using the default Ubuntu Wayland session
crashed GNOME Shell with `signal 11` after DRI/Vulkan driver errors.

The anti-jam GUI is installed as a desktop autostart entry for that RDP session.
It waits 20 seconds for the desktop to settle, then runs:

```bash
cd /home/qvise/antijamming
./run_realtime.sh
```

If you need to restart the GUI manually, open a terminal inside the RDP desktop
and run:

```bash
cd /home/qvise/antijamming
./run_realtime.sh
```

For non-interactive receiver diagnostics over SSH, use the offscreen Qt path:

```bash
QT_QPA_PLATFORM=offscreen ./run_realtime.sh --auto-start --auto-stop-after-s 170 --quit-after-stop
```

The launcher rejects SSH X11 forwarding by default. To force that old transport
for a short test, set `ANTIJAM_ALLOW_SSH_X11=1`; expect slow or fragile GUI
behavior. The supported operator path is the configured GNOME RDP desktop.

Run phase calibration from the sibling repo:

```bash
../phase-calibration/run_calibration.sh
```

The launcher rejects runtime flags. Skip pre-launch USRP checks only when you
need a fast local UI startup:

```bash
ANTIJAMMING_SKIP_USRP_PREFLIGHT=1 ./run_realtime.sh
```

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
those duplicates. When an optional experiment overlay is selected, the runtime
also records its sample rate and bandwidth from this same product setting.

Bench geometry and external transmitter settings are not runtime controls. They
live in optional files under `configs/experiments/`, not in the product profile.
To reproduce the preserved measured bladeRF/jammer manifest, launch with:

```bash
ANTIJAM_RUNTIME_OVERLAY=configs/experiments/realtime_measured_bladerf_preserve_lcmv_test.json ./run_realtime.sh
```

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

The anti-jamming backend owns the USRP. GNSS-SDR reads the single FIFO IQ stream
provided by the backend. Direct-USRP and RTL-SDR GNSS-SDR configs are not part of
the product runtime.

In normal realtime mode, GNSS-SDR runs.

Calibration is not a realtime pipeline mode. The standalone
`../phase-calibration` repo owns phase calibration capture, SynthUSB control,
and sweep tooling. This realtime repo consumes the exported calibration artifact
checked in at:

```text
configs/calibration/x300_phase_offsets_100khz.json
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
