# X310 + TwinRX LO Sharing README

This README explains the physical LO cables, UHD channel mapping, and the software LO map that produced phase-stable four-channel operation on the current X310 + two TwinRX setup.

Phase calibration capture and sweep tooling lives in the sibling
`../phase-calibration` repository. This realtime repository consumes exported
calibration artifacts from `configs/calibration/`.

## 1. Short Answer

Use the E2 LO-sharing map:

```python
rx_lo_sources_by_channel = (
    "internal",    # ch0 / A:0
    "companion",  # ch1 / A:1
    "reimport",   # ch2 / B:0
    "reimport",   # ch3 / B:1
)

rx_lo_exports_by_channel = (
    True,   # ch0 exports/enables A-side LO path
    False,  # ch1 does not export
    True,   # ch2 exports/enables B-side reimport path
    False,  # ch3 does not export
)
```

This is the default in:

```text
src/antijamming/config/schemas/runtime.py
../phase-calibration/src/phase_calibration/capture.py
src/antijamming/app/main.py
```

The default static phase-calibration file is:

```text
configs/calibration/x300_phase_offsets_100khz.json
```

## 2. Physical TwinRX LO Connectors

Each TwinRX has two LO stages:

```text
LO1
LO2
```

The TwinRX connector meanings are:

| Connector | Meaning |
| --- | --- |
| `J1` | LO2 Export |
| `J2` | LO2 Input |
| `J3` | LO1 Export |
| `J4` | LO1 Input |

For two TwinRX boards in one X300/X310, the documented neighbor-sharing cable table is:

| Direction | LO stage | Cable |
| --- | ---: | --- |
| A to B | LO2 | A `J1 LO2 Export` to B `J2 LO2 Input` |
| B to A | LO2 | B `J1 LO2 Export` to A `J2 LO2 Input` |
| A to B | LO1 | A `J3 LO1 Export` to B `J4 LO1 Input` |
| B to A | LO1 | B `J3 LO1 Export` to A `J4 LO1 Input` |

Local references:

```text
references/twinrx/twinrx_getting_started.pdf
references/twinrx/ettus_uhd_twinrx_manual.html
```

Public references:

```text
https://uhd.readthedocs.io/en/latest/page_twinrx.html
https://kb.ettus.com/TwinRX
```

## 3. UHD Channel Mapping

Runtime readback reports this channel-to-board mapping:

| UHD channel | Board | TwinRX channel |
| ---: | --- | ---: |
| `ch0` | Daughterboard A | A:0 |
| `ch1` | Daughterboard A | A:1 |
| `ch2` | Daughterboard B | B:0 |
| `ch3` | Daughterboard B | B:1 |

So every four-value tuple is ordered as:

```text
(ch0, ch1, ch2, ch3)
```

or physically:

```text
(A:0, A:1, B:0, B:1)
```

The antenna mapping used by default is:

```text
ch0: RX1
ch1: RX2
ch2: RX1
ch3: RX2
```

## 4. What `rx_lo_sources_by_channel` Means

`rx_lo_sources_by_channel` tells UHD where each channel should get its LO from.

| Source | Meaning |
| --- | --- |
| `internal` | This channel uses/generates an internal LO. |
| `companion` | This channel uses the LO from the companion channel on the same TwinRX board. |
| `external` | This channel uses the external LO input routing path. |
| `reimport` | TwinRX-specific UHD LO reimport routing mode exposed by this UHD/device stack. |
| `disabled` | LO path disabled. |

The current UHD readback exposes:

```text
internal
external
companion
disabled
reimport
```

Important distinction:

```text
physical cables create the possible LO path
UHD source/export settings decide the internal LO switch routing
```

So correct cables alone are not enough. The software LO source/export map must also route LO1 and LO2 coherently.

## 5. What `rx_lo_exports_by_channel` Means

`rx_lo_exports_by_channel` tells UHD whether a channel should export or enable an LO output path.

Example:

```python
rx_lo_exports_by_channel = (True, False, True, False)
```

means:

| Channel | Export? | Meaning |
| ---: | --- | --- |
| `ch0` | `True` | ch0 exports/enables an A-side LO path. |
| `ch1` | `False` | ch1 does not export. |
| `ch2` | `True` | ch2 exports/enables a B-side reimport path. |
| `ch3` | `False` | ch3 does not export. |

Observed UHD rule on this system:

```text
Do not set export=True on both channels of the same TwinRX board.
```

The invalid E/G tests failed before capture with:

```text
Cannot export LOs for both channels
```

So the practical rule is:

```text
one export per TwinRX board maximum
```

## 6. Old Failing Map

The original map was:

```python
rx_lo_sources_by_channel = (
    "internal",
    "companion",
    "external",
    "external",
)

rx_lo_exports_by_channel = (
    True,
    False,
    False,
    False,
)
```

Table:

| Channel | Board | Source | Export | Intended meaning |
| ---: | --- | --- | --- | --- |
| `ch0` | A:0 | `internal` | `True` | A:0 generates LO and exports. |
| `ch1` | A:1 | `companion` | `False` | A:1 uses A:0 LO. |
| `ch2` | B:0 | `external` | `False` | B:0 uses external LO input routing. |
| `ch3` | B:1 | `external` | `False` | B:1 uses external LO input routing. |

The intended physical story was:

```text
A exports LO1/LO2 through A J1/J3
B receives LO1/LO2 through B J2/J4
```

But the measured result failed:

```text
ch0/ch1 stable
ch2/ch3 stable
A-board pair versus B-board pair drifted about 117 deg/s
```

That is not a fixed phase offset. It is a board-to-board phase ramp. A static calibration file cannot fix a ramp.

Diagnostic files:

```text
logs/calibration/direction_ab_current_cables_240.json
logs/calibration/direction_ab_current_cables_240.csv
logs/calibration/direction_ba_current_cables_240.json
logs/calibration/direction_ba_current_cables_240.csv
```

## 7. Working E2 Map

The working E2 map is:

```python
rx_lo_sources_by_channel = (
    "internal",
    "companion",
    "reimport",
    "reimport",
)

rx_lo_exports_by_channel = (
    True,
    False,
    True,
    False,
)
```

Table:

| Channel | Board | Source | Export | Meaning |
| ---: | --- | --- | --- | --- |
| `ch0` | A:0 | `internal` | `True` | A:0 generates LO and exports/enables A-side LO path. |
| `ch1` | A:1 | `companion` | `False` | A:1 uses A:0 LO on same board. |
| `ch2` | B:0 | `reimport` | `True` | B:0 uses TwinRX reimport routing and enables B-side path. |
| `ch3` | B:1 | `reimport` | `False` | B:1 uses reimport routing; no second export. |

Measured result:

```text
phase_offsets_std_deg ~= [0.000, 0.042, 0.024, 0.026]
drift rates near 0 deg/s
quality_pass = true
```

Proof files:

```text
logs/calibration/map_e2_reimport_both_b_one_export_240.json
logs/calibration/map_e2_reimport_both_b_one_export_240.csv
```

The regenerated canonical calibration file also uses E2:

```text
configs/calibration/x300_phase_offsets_100khz.json
```

with:

```text
phase_offsets_deg = [0.00, 171.69, 42.88, 81.91]
phase_offsets_std_deg = [0.00, 0.02, 0.02, 0.01]
quality_pass = true
```

## 8. Why `reimport` Matters Here

The physical cables were correct, but the old `external,external` software map did not produce phase coherence.

The measured conclusion for this setup is:

```text
Do not use external/external for board B in the current X310 + 2x TwinRX configuration.
Use reimport/reimport with one export enabled on board B.
```

This does not mean `external` is always wrong on all systems. It means this UHD/TwinRX stack and cable state measured stable with E2 and unstable with the old map.

The safe engineering rule is:

```text
Trust measured phase stability, not the map that merely looks logical.
```

## 9. Required Startup Sequence

The runtime should follow this sequence:

```text
1. Use subdevice/channel order A:0 A:1 B:0 B:1.
2. Use antennas ch0:RX1, ch1:RX2, ch2:RX1, ch3:RX2.
3. Enable TwinRX LO sharing.
4. Set E2 LO source/export map.
5. Tune all RX channels under one UHD command_time.
6. Wait until all reported LO sensors are locked.
7. Start streams using a timed stream command.
8. For calibration, use SynthUSB3 splitter tone.
9. Save static phase offsets only if phase std, drift, and SNR pass.
```

The implementation does timed tune and timed start in:

```text
src/antijamming/radio/usrp/device.py
```

## 10. Calibration Model

The splitter calibration model is:

```text
measured_phase = fixed_hardware_phase_offset
```

because all channels receive the same conducted reference tone.

The antenna runtime model is:

```text
measured_phase = true_spatial_phase + fixed_hardware_phase_offset
```

Static calibration removes only the fixed hardware offset:

```text
calibrated_phase = measured_phase - fixed_hardware_phase_offset
```

It must not remove live spatial phase differences. Those spatial phase differences are what MUSIC/LCMV uses for DoA and beamforming.

## 11. Important Checks

Use the GUI `Phase Check` or run:

```bash
cd ../phase-calibration
PYTHONPATH=src python -m phase_calibration.capture \
  --gain-db 60 \
  --synth-power-dbm -20 \
  --warmup-chunks 100 \
  --capture-chunks 240 \
  --samples-per-chunk 32768 \
  --output logs/calibration/manual_phase_check.json \
  --diagnostic-csv logs/calibration/manual_phase_check.csv
```

Expected LO map in console:

```text
TwinRX LO sharing map: ch0:internal/export=True,ch1:companion/export=False,ch2:reimport/export=True,ch3:reimport/export=False
```

Expected passing quality:

```text
quality_pass = true
max non-reference phase_offsets_std_deg < 3 deg
tone_snr_db >= 10 dB
drift rates near 0 deg/s
```

Do not use a calibration file if it says:

```text
quality_pass = false
```

The loader refuses invalid files in:

```text
src/antijamming/dsp/phase/alignment.py
```

## 12. Practical Meaning

The system is not phase-coherent just because it is an X310.

The system is phase-coherent only when:

```text
physical LO cables are installed
correct UHD LO routing is applied
phase trace proves low std and near-zero drift
static calibration file passes quality checks
```

For this setup, the measured working state is:

```text
LO map: E2
calibration file: configs/calibration/x300_phase_offsets_100khz.json
runtime default: uses E2 and loads the canonical calibration file
```
