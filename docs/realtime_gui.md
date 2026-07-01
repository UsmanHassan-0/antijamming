# Realtime Local GUI

This repository runs the X300/TwinRX anti-jamming runtime through a local PyQt GUI.

## Product Commands

Run the normal realtime product:

```bash
./run_realtime.sh
```

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

The USRP address is fixed to the known X300/XG 10GbE SFP path:

```json
"usrp_addr": "addr=192.168.40.2"
```

The host-side 10GbE profile uses `192.168.40.1/24` on the SFP+ NIC. The runtime
uses fixed UHD transport frame sizes of 8000 bytes for this profile; it does not
search for another USRP address.

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
