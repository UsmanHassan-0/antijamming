# RF Hardware Boundary

The realtime profile targets GPS L1 at `1575.42 MHz` with `4 MHz` receive
bandwidth. It configures the X300/TwinRX receive chain; it does not control the
external bladeRF transmitter, jammer, attenuator, antenna placement, cables, or
distance.

The runtime therefore does not accept or calculate bladeRF gain, jammer
attenuation, transmitter power, path loss, receive-chain loss, or a link
budget. Those values do not select MUSIC directions or LCMV constraints.
Operator markers record only externally confirmed physical state transitions
such as jammer on/off or bladeRF on/off, plus an optional note.

Dated bench measurements and assumptions remain historical evidence under
`docs/audits/`. They must not be interpreted as current runtime configuration
or as measurements made by this application. In particular, bladeRF software
gain is not RF output power, a modelled free-space loss is not a received-power
measurement, and a mathematical LCMV null is not measured OTA suppression.

At the digital receive boundary, UHD is configured for `fc32` host samples and
`sc16` wire samples. Every receive result is validated for the configured
channel count, two-dimensional `complex64` data, and an exact metadata/sample
count match before IQ reaches DSP or GNSS consumers. Overflow and timeout are
explicit transport states. Any other UHD metadata state is not treated as
ordinary IQ. These are software contracts; the attached X300 run is the
separate evidence gate for UHD behavior and timing.
