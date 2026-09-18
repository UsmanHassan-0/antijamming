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

The normal launcher assumes a desktop launch. It does not precheck display
variables or print desktop/offscreen advice. Explicit developer platform
settings are still passed to Qt; no invisible GUI fallback is selected when
a desktop is missing. Qt handles display initialization, and an application
failure remains a nonzero launcher exit. Native Qt errors are not suppressed.
When logging is enabled, the GUI records its actual Qt platform in
`logs/app.log` rather than printing a window-visibility message.

For developer diagnostics that need to exercise the GUI without showing a
window, use the offscreen Qt path:

```bash
QT_QPA_PLATFORM=offscreen ./run_realtime.sh --auto-start --auto-stop-after-s 170 --quit-after-stop
```

This still creates the GUI process and its separate headless backend. Direct
headless operation uses `antijamming.app.headless` alone, with the appropriate
UHD environment and an IPC controller (or explicit `--auto-start`). It does not
need a desktop or Qt offscreen mode. Both paths use `BackendRuntime`; a client
integration can control that service without opening this repository's GUI.

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

## Operator and developer UX boundary

Use the same receiver implementation for desktop operators and headless
integrations. A development branch is not a second, different receiver for
customers. Release identity must include the deployed source/build, not only
the repository beside it: an installed bundle can contain older code.

The current launcher keeps routine display/platform advice out of the terminal;
explicit offscreen developer operation remains available. Keep actionable
failure status visible. Quiet presentation must not hide a failed receiver or
disable live GNSS state needed by the plots.

The fixed profile defaults to `logging_enabled=false` for normal operation.
This disables application saved logs, including saved errors and new run
archives; it does not delete existing files. Live receiver stdout parsing,
UDP/NMEA data, FIFOs and GUI/headless state remain operational. This is not a
third "current logs but no archives" mode. An external controller's console
capture can still append independently of this application switch.

For development, set `logging_enabled=true`: opening the service appends to
current logs, an accepted new receiver Start resets them, and Stop preserves a
run archive. Those development archives are not automatically pruned.

The following are continuing UX requirements, **not implemented guarantees**:

- Preserve visible failure states and live receiver data even when saved
  development logs are off. The September 18 decision supersedes the earlier
  current-run-only logging proposal; it does not authorize deleting old logs.
- Keep slow file reads, archive operations and expensive analysis out of the
  GUI event loop. Limit plot refresh work to useful new data and visible views;
  establish responsiveness with measurements, not just a timer setting.
- Give Start/Stopping/Stopped/Failed states consistent meanings in the GUI and
  headless controller. Accepted Stop is not proof that shutdown has completed.
- Preserve these boundaries in packaged releases. Building again must not be
  assumed to clear accumulated logs or deploy the current source checkout.

The [NADS 2 controller inspection](audits/launcher_lo_review.md#nads-2-controller-and-installed-logs--september-18)
records the external integration's actual behavior and diagnostic limits. No
NADS 2 implementation or logging policy was changed during that inspection.

## Runtime Profile

LCMV protection is automatic on the cleanup branch. There is no LCMV checkbox,
manual enable/disable command, or arming switch in the profile. The read-only
status distinguishes waiting for a healthy GNSS/reference baseline, armed
uniform output, active protection, and released uniform recovery. The output
path label names the automatic shared-beam architecture; it does not claim that
nulling is active. Start/Stop still controls the whole receiver session.

Arming freezes a fresh, stable measured reference after healthy PVT,
observations and C/N0 checks. Arming alone applies no null. Subsequent jammer
evidence activates protection; sustained low evidence releases it. New evidence
can reactivate protection without a button or a new reference. Stop/Start
creates a new run and collects a new reference automatically. Physical RF
marker buttons record observations only; they do not activate protection.

See [automatic-control evidence](audits/automatic_lcmv_control.md) for the
software-tested scope and remaining hardware/latch limitations.

Product runtime values live in:

```text
configs/antijamming/x300_realtime.jsonc
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
