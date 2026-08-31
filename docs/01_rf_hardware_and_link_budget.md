# RF Hardware And Link Budget

The realtime profile targets GPS L1 at `1575.42 MHz` with `4 MHz` receive bandwidth. Bench geometry and external bladeRF/jammer settings are not runtime controls. The preserved optional overlay `configs/experiments/realtime_measured_bladerf_preserve_lcmv_test.json` records the jammer basis as `9.51 dBm` at `1575.42 MHz` over `4 MHz`, plus attenuation, distance, antenna gains, chain losses, LNA gain, and USRP gain.

BladeRF software gain and RF output power are not the same thing. The loggable RF budget in `src/antijamming/rf/budget.py` separates configured transmitter/generator settings from estimated power at the receive antenna and USRP input. This matters because a bladeRF gain number is a control value; the jammer power used for J/S must be a measured or calibrated RF power basis.

The link budget terms are:

- Full-band jammer power: integrated jammer power over the configured occupied bandwidth.
- Peak/bin power: narrowband or spectral-peak value used for spectral safety checks.
- FSPL: free-space path loss from jammer transmitter to receive antenna distance.
- Antenna gains: transmitter and receive antenna gain terms.
- Chain losses: cables, filters, DC blocks, splitters, and other passive loss.
- LNA gain: gain ahead of the USRP input.
- USRP input: estimated RF power presented to the X300/TwinRX input.
- J/S: jammer-to-signal ratio, not the same as absolute input power.

Absolute USRP input power answers “will I overload or clip the receiver chain?” J/S answers “how much stronger is the jammer than the GNSS signal?” A low absolute power can still be high J/S because GNSS signals are extremely weak.
