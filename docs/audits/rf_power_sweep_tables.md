# RF Power, Compression, and J/S Sweep Tables

These tables use the laptop calculator functions from
`/home/u/receiver_fspl_calculator/app.py` and report powers at explicit RF
reference planes. They are calculations, not replacements for direct power
measurements at the LNA and TwinRX inputs.

## Assumptions and reference planes

| Parameter | Value |
| --- | ---: |
| Center frequency / processed bandwidth | 1575.42 MHz / 4 MHz |
| Jammer distance used | 14 ft = 4.2672 m minimum |
| bladeRF distance | 11 ft 5 in = 3.4798 m |
| Jammer / bladeRF TX antenna gain | 2 dBi / 2 dBi |
| Receive antenna gain | 5 dBi |
| Jammer 4 MHz RMS source power | 9.51 dBm before attenuation |
| Jammer positive-peak detector reading | 21.4 dBm before attenuation |
| Jammer total-band RMS source power | 17.7 dBm RMS over 1427-1610 MHz |
| bladeRF measured calibration | -47.3 dBm over 50 MHz at SW gain 50 |
| Cable loss | 2 dB total, assumed 1 dB before and 1 dB after LNA |
| BPF loss at L1 | 2 dB typical |
| LNA gain | 50 dB |
| LNA input/output P1dB | -30.2 / +19.8 dBm |
| DC-block loss | 0.5 dB |
| TwinRX maximum RF input | +10 dBm |
| Calculator FSPL, jammer / bladeRF | 48.999 / 47.227 dB |

The 9.51 dBm jammer value is treated as an independent same-band RMS input.
It is **not** derived from 17.7 dBm over 183 MHz in these tables. If the jammer
PSD were flat, the mathematical conversion would instead be
`17.7 + 10 log10(4/183) = 1.10 dBm` in 4 MHz. Therefore 9.51 dBm must remain
labeled as a separate measured/integrated band value; if it was not directly
measured or PSD-integrated, the 4 MHz J/S table must be recalculated.

The bladeRF file/sample center at 1584 MHz does not move the GNSS L1 component
from 1575.42 MHz; L1 occupies an offset within that sampled waveform. FSPL for
the L1 component therefore uses 1575.42 MHz. Even substituting 1584 MHz would
change FSPL by only `20 log10(1584/1575.42) = 0.047 dB`. Center-frequency
changes do not perform bandwidth-power conversion; that conversion depends on
integrated bandwidth and PSD.

The receive chain used for the stage tables is:

```text
RX antenna terminal
  -> 1 dB pre-LNA cable
  -> 2 dB L1 BPF
  -> LNA input
  -> 50 dB LNA
  -> LNA output
  -> 0.5 dB DC block
  -> 1 dB post-LNA cable
  -> TwinRX/USRP RF input
```

The actual allocation of the confirmed 2 dB total cable loss has not been
measured. Splitting it 1 dB/1 dB preserves the exact calculated TwinRX power,
but LNA-input power moves by up to 1 dB if the real split differs.

## Jammer 4 MHz RMS power through the receiver chain

All powers are dBm and attenuation is dB. This table uses the 9.51 dBm 4 MHz
jammer RMS measurement. It is the appropriate jammer basis for the digital
4 MHz J/S. The separate 17.7 dBm total-band RMS measurement is used to bound
analog front-end loading.

| Att. | TX after att. | RX antenna terminal | Before LNA | LNA P1dB exceeded by 4 MHz RMS? | After LNA ideal | USRP input | Greater than TwinRX +10 dBm max? |
| ---: | ---: | ---: | ---: | :---: | ---: | ---: | :---: |
| 0 | 9.51 | -32.49 | -35.49 | No | 14.51 | 13.01 | **YES** |
| 10 | -0.49 | -42.49 | -45.49 | No | 4.51 | 3.01 | No |
| 20 | -10.49 | -52.49 | -55.49 | No | -5.49 | -6.99 | No |
| 30 | -20.49 | -62.49 | -65.49 | No | -15.49 | -16.99 | No |
| 40 | -30.49 | -72.49 | -75.49 | No | -25.49 | -26.99 | No |
| 50 | -40.49 | -82.49 | -85.49 | No | -35.49 | -36.99 | No |
| 60 | -50.49 | -92.49 | -95.49 | No | -45.49 | -46.99 | No |
| 70 | -60.49 | -102.49 | -105.49 | No | -55.49 | -56.99 | No |
| 80 | -70.49 | -112.49 | -115.49 | No | -65.49 | -66.99 | No |
| 90 | -80.49 | -122.49 | -125.49 | No | -75.49 | -76.99 | No |

## LNA compression and TwinRX safety

The 17.7 dBm value is a **total-band RMS power**, not a peak. The first table
below propagates it as a conservative analog-loading upper bound by assuming
zero BPF rejection across all jammer energy. The real post-BPF RMS power
requires integration of jammer PSD times the measured BPF response.

| Att. | Total-band RMS before LNA, upper bound | LNA compressed by RMS bound? | RMS USRP ideal, upper bound | Greater than TwinRX +10 dBm max? |
| ---: | ---: | :---: | ---: | :---: |
| 0 | -25.30 | **YES** | 23.20 invalid | **YES** |
| 10 | -35.30 | No | 13.20 | **YES** |
| 20 | -45.30 | No | 3.20 | No |
| 30 | -55.30 | No | -6.80 | No |
| 40 | -65.30 | No | -16.80 | No |
| 50 | -75.30 | No | -26.80 | No |
| 60 | -85.30 | No | -36.80 | No |
| 70 | -95.30 | No | -46.80 | No |
| 80 | -105.30 | No | -56.80 | No |
| 90 | -115.30 | No | -66.80 | No |

The separate 21.4 dBm value is retained only as the stated positive-peak
detector reading. Treating that reading as in-band at L1 and applying the
2 dB L1 BPF insertion loss gives this separate positive-detector check:

| Att. | Positive peak before LNA | LNA P1dB exceeded? | Positive peak at USRP ideal | Greater than TwinRX +10 dBm max? |
| ---: | ---: | :---: | ---: | :---: |
| 0 | -23.60 | **YES** | 24.90 invalid | **YES** |
| 10 | -33.60 | No | 14.90 | **YES** |
| 20 | -43.60 | No | 4.90 | No |
| 30 | -53.60 | No | -5.10 | No |
| 40 | -63.60 | No | -15.10 | No |
| 50 | -73.60 | No | -25.10 | No |
| 60 | -83.60 | No | -35.10 | No |
| 70 | -93.60 | No | -45.10 | No |
| 80 | -103.60 | No | -55.10 | No |
| 90 | -113.60 | No | -65.10 | No |

At 0 dB attenuation the ideal post-LNA values are invalid because the LNA is
already beyond its input P1dB. At the LNA output P1dB of +19.8 dBm, the
corresponding TwinRX input would already be approximately +18.3 dBm after
post-LNA losses, above the +10 dBm maximum.

In the RMS table, `No` means only that the calculated RMS value does not exceed
the stated maximum. It is not proof that every instantaneous waveform peak is
below +10 dBm; that needs a calibrated peak/PAPR measurement at the TwinRX
reference plane.

Under the assumed 1 dB/1 dB cable split:

- The conservative total-band RMS bound becomes nominally linear above
  4.90 dB attenuation and drops below the TwinRX maximum above 13.20 dB.
- The positive-peak check becomes nominally linear above 6.61 dB attenuation
  and drops below the TwinRX maximum above 14.90 dB.
- The preferred LNA-input comfort target of no more than -40 dBm requires
  16.40 dB attenuation. Allowing for the unknown cable split raises the
  conservative requirement to 17.40 dB.
- Therefore 0 dB is unsafe, 10 dB is still unsafe for TwinRX, 20 dB is the
  lowest modeled comfortable setting, and 30 dB has substantially more margin.

## bladeRF gain and power through the receiver chain

The connected bladeRF 2.0 reports a TX overall-gain range of -23.75 to +66 dB.
Only the -47.3 dBm total-50-MHz value at software gain 50 is the confirmed
measurement for this file. Every other row assumes 1 dB RF-output change for
each 1 dB software-gain change. The 4 MHz conversion also assumes flat power
spectral density:

```text
P_blade_total_50MHz(G) = -47.3 + (G - 50) dBm
P_blade_4MHz(G) = P_blade_total_50MHz(G) + 10 log10(4/50)
                 = G - 108.2691 dBm
```

At low software gains, actual transmitter noise/leakage can invalidate this
linear extrapolation. A spectrum-analyzer sweep is needed to calibrate these
rows as measured values.

| SW gain | TX total 50 MHz | TX estimated 4 MHz | RX antenna 4 MHz | Before LNA 4 MHz | LNA P1dB exceeded? | After LNA 4 MHz | USRP 4 MHz | Greater than TwinRX +10 dBm max? |
| ---: | ---: | ---: | ---: | ---: | :---: | ---: | ---: | :---: |
| -23.75 | -121.05 | -132.02 | -172.25 | -175.25 | No | -125.25 | -126.75 | No |
| -20 | -117.30 | -128.27 | -168.50 | -171.50 | No | -121.50 | -123.00 | No |
| -10 | -107.30 | -118.27 | -158.50 | -161.50 | No | -111.50 | -113.00 | No |
| 0 | -97.30 | -108.27 | -148.50 | -151.50 | No | -101.50 | -103.00 | No |
| 10 | -87.30 | -98.27 | -138.50 | -141.50 | No | -91.50 | -93.00 | No |
| 20 | -77.30 | -88.27 | -128.50 | -131.50 | No | -81.50 | -83.00 | No |
| 30 | -67.30 | -78.27 | -118.50 | -121.50 | No | -71.50 | -73.00 | No |
| 40 | -57.30 | -68.27 | -108.50 | -111.50 | No | -61.50 | -63.00 | No |
| 50 | -47.30 | -58.27 | -98.50 | -101.50 | No | -51.50 | -53.00 | No |
| 60 | -37.30 | -48.27 | -88.50 | -91.50 | No | -41.50 | -43.00 | No |
| 66 | -31.30 | -42.27 | -82.50 | -85.50 | No | -35.50 | -37.00 | No |

The maximum bladeRF setting is far below both hardware limits in this model.
It does not change the low-attenuation jammer safety conclusion.

## Modeled 4 MHz RMS J/S at the TwinRX/USRP RF input

The matrix entries are same-band RMS ratios in dB. Positive means the jammer
is stronger; negative means the bladeRF signal is stronger. The 17.7 dBm
total-band jammer RMS number is deliberately **not** used here. J/S uses the
9.51 dBm jammer RMS power measured over the receiver's 4 MHz bandwidth and an
estimated bladeRF power over that same 4 MHz.

This is a modeled matrix, not a measured J/S matrix. Only the bladeRF
-47.3 dBm total-50-MHz value at gain 50 was measured. Converting it to 4 MHz
assumes flat PSD, and every other gain row additionally assumes a one-for-one
change between software gain and RF output power.

J/S alone cannot determine hardware safety because it is a ratio, not an
absolute input power. Under the conservative total-band RMS loading model,
the entire 0 dB attenuation column is unsafe because the LNA is compressed,
and the entire 10 dB column is unsafe because the ideal TwinRX input exceeds
+10 dBm. Columns from 20 through 90 dB are below both modeled limits. Every
bladeRF gain row is below both limits when the bladeRF is considered alone.

| bladeRF gain / jammer attenuation | 0 UNSAFE | 10 UNSAFE | 20 OK | 30 OK | 40 OK | 50 OK | 60 OK | 70 OK | 80 OK | 90 OK |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -23.75 | 139.76 | 129.76 | 119.76 | 109.76 | 99.76 | 89.76 | 79.76 | 69.76 | 59.76 | 49.76 |
| -20 | 136.01 | 126.01 | 116.01 | 106.01 | 96.01 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 |
| -10 | 126.01 | 116.01 | 106.01 | 96.01 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 |
| 0 | 116.01 | 106.01 | 96.01 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 |
| 10 | 106.01 | 96.01 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 |
| 20 | 96.01 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 | 6.01 |
| 30 | 86.01 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 | 6.01 | -3.99 |
| 40 | 76.01 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 | 6.01 | -3.99 | -13.99 |
| 50 | 66.01 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 | 6.01 | -3.99 | -13.99 | -23.99 |
| 60 | 56.01 | 46.01 | 36.01 | 26.01 | 16.01 | 6.01 | -3.99 | -13.99 | -23.99 | -33.99 |
| 66 | 50.01 | 40.01 | 30.01 | **20.01** | 10.01 | 0.01 | -9.99 | -19.99 | -29.99 | -39.99 |

The formula is obtained from the two explicit 4 MHz RMS reference-plane
powers:

```text
P_J,USRP,4MHz(A) = 13.0114 - A dBm RMS
P_S,USRP,4MHz(G) = G - 102.9959 dBm RMS  [modeled]

J/S = P_J,USRP,4MHz - P_S,USRP,4MHz
    = 116.0073 - A - G dB
```

Thus the algebra of the displayed matrix is consistent, but its bladeRF side
is not experimentally established across the gain range. A direct 4 MHz RMS
measurement at each bladeRF gain must replace the modeled `P_S` values before
calling the matrix measured J/S.

For example, row `66`, column `30` uses the two explicitly modeled USRP-input
powers `P_J = -16.99 dBm RMS` and `P_S = -37.00 dBm RMS`, both over 4 MHz:

```text
J/S = -16.99 - (-37.00) = +20.01 dB
```

That positive result means only that J is 20.01 dB stronger than S in the same
4 MHz band. It does not say whether TwinRX is safe. Safety comes from the
absolute-power tables: the modeled jammer setting at 30 dB attenuation is below
the LNA and TwinRX limits, whereas the 0 dB and 10 dB columns are marked unsafe.

## Combined 4 MHz J + S power at the TwinRX input

These entries are total 4 MHz RMS power in dBm, not J/S. They combine powers
in linear units. They do not replace the positive-peak/total-band safety tables
above.

| bladeRF gain / jammer attenuation | 0 | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -23.75 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.99 | -66.99 | -76.99 |
| -20 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.99 | -66.99 | -76.99 |
| -10 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.99 | -66.99 | -76.99 |
| 0 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.99 | -66.99 | -76.98 |
| 10 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.99 | -66.98 | -76.88 |
| 20 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.99 | -56.98 | -66.88 | -76.02 |
| 30 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.99 | -46.98 | -56.88 | -66.02 | -71.54 |
| 40 | 13.01 | 3.01 | -6.99 | -16.99 | -26.99 | -36.98 | -46.88 | -56.02 | -61.54 | -62.83 |
| 50 | 13.01 | 3.01 | -6.99 | -16.99 | -26.98 | -36.88 | -46.02 | -51.54 | -52.83 | -52.98 |
| 60 | 13.01 | 3.01 | -6.99 | -16.98 | -26.88 | -36.02 | -41.54 | -42.83 | -42.98 | -42.99 |
| 66 | 13.01 | 3.01 | -6.98 | **-16.95** | -26.58 | -33.98 | -36.58 | -36.95 | -36.99 | -37.00 |

## Values requiring direct measurement

- Exact bladeRF 4 MHz power at every software gain, especially gain 66.
- Exact pre-LNA versus post-LNA allocation of the confirmed 2 dB cable loss.
- Jammer PSD across 1427-1610 MHz and the measured BPF S21 curve for integrated
  full-band LNA loading.
- Conducted power at the LNA input and TwinRX input to validate the free-space
  model, antenna gains, polarization, mismatch, near-field effects, and room
  multipath.
