# `someone.md` Layer-by-Layer Audit

The laptop source `/home/u/someone.md` was copied byte-for-byte and verified
with SHA-256
`a2e3cfb7980ae00c28aaecb7e7475f288c71f3647d0bcdb51aaafd63c3b60dbc`.
It is 26,596 bytes and 2,717 lines. The source is preserved at
`/home/tramiq_sdr/someone.md`; this audit does not modify it.

## RF-budget claims

The document's rule `received dBm = transmitted dBm + gains - losses` and its
warning that bladeRF software gain 66 is not 66 dBm are correct. Its displayed
numerical budgets are obsolete because they used a shared 4 m path, zero-dBi
antennas, 0.55 dB pre/post cables, -47.84 dBm as though it were a 4 MHz bladeRF
measurement, and a +16 dB extrapolation from that basis.

The current explicit assumptions are instead:

- bladeRF: 11 ft 5 in (3.4798 m), 2 dBi TX antenna, 5 dBi RX antenna;
- jammer: at least approximately 14 ft (4.2672 m), 2 dBi TX antenna, 5 dBi RX
  antenna;
- receive chain: 1 dB assumed pre-LNA cable, 2 dB BPF, 50 dB LNA, 0.5 dB DC
  block, and 1 dB assumed post-LNA cable;
- bladeRF measurement: -47.3 dBm RMS over 50 MHz at gain 50; gain 66 and 4 MHz
  values are extrapolations, not additional measurements;
- jammer: 17.7 dBm RMS over 1427-1610 MHz, 21.4 dBm positive-peak reading, and
  a separately stated 9.51 dBm RMS value for 4 MHz.

The old document implicitly treated 9.51 dBm as a confirmed 4 MHz measurement.
That value is not derivable from 17.7 dBm over 183 MHz: flat-PSD scaling gives
`17.7 + 10 log10(4/183) = 1.10 dBm`. Therefore 9.51 dBm remains an independent
input that must be confirmed by a same-band measurement or PSD integration.
The corrected complete tables are in `docs/12_rf_power_sweep_tables.md`.

## Total bladeRF power versus useful GNSS

The document is correct that total 4 MHz bladeRF power is not an individual
satellite carrier power. A GNSS receiver tracks each PRN through its correlation
gain and reports C/N0; it does not use total file power as `C`. Consequently the
RF input J/S table is a same-band hardware-loading ratio, while PVT survivability
must also be judged from per-PRN C/N0, tracking lock, cycle-slip flags, Doppler,
carrier phase, code phase, and used-in-fix count.

The runtime now archives those tracking fields under
`<log_dir>/tracking-state/tracking_<UTC>_<pid>.jsonl`. Historical runs cannot be
given carrier-phase evidence retroactively because that archive did not exist.

## Leakage-floor proposal

The document's 90 dB leakage-floor hypothesis is physically valid but was not
tested. Terminating the attenuator output with a rated shielded 50-ohm load while
the jammer electronics remain active would distinguish intentional antenna
radiation from chassis/cable/power/ground leakage. That requires an appropriate
load and an operator-controlled bench change; it is not a software test and is
still incomplete.

The proposed raw-channel matrix was not run because the operator explicitly
rejected a separate raw-channel experiment. The live validation instead uses
the calibrated uniform four-channel combiner as the healthy baseline, which is
the actual product input to GNSS-SDR.

## LCMV implementation: historical statement versus current code

The document was correct about the historical failure mechanism: preserving
`[1,1,1,1]` does not necessarily preserve an off-axis bladeRF spatial vector.
That statement is now obsolete for the configured product path.

Current code has one shared full-covariance solver in
`src/antijamming/dsp/beamforming/lcmv.py` implementing
`R^-1 C (C^H R^-1 C)^-1 f`. It has only two thin null-vector inputs:

1. `covariance_lcmv_ideal`: an ideal steering vector at the chosen jammer
   bearing;
2. `covariance_lcmv_measured_u1`: a measured dominant U1 null vector.

Both use the same solver and measured covariance. There is no identity-
covariance shortcut, covariance-free selectable method, or separate
constraint-only weight equation. The configured preserve mode is
`realtime_bladerf_measured_u1`, so the arm-time measured bladeRF vector is the
preserve constraint. The configured product null input remains ideal steering;
measured U1 remains the second diagnostic/selectable input for comparing the
dominant measured vector with the ideal steering model.

For clarity, the historical parent commit contained the five non-uniform
weight-generation entry points that prompted the earlier discussion:
`legacy_constraint_null_ideal_weights`,
`uniform_preserving_vector_null_weights`,
`uniform_preserving_covariance_lcmv_null_weights`,
`uniform_preserving_covariance_vector_null_weights`, and
`legacy_angle_fan_diagnostic_weights`. Uniform combining was a sixth separate
helper. The first, second, and fifth were legacy/covariance-free paths. The two
covariance functions duplicated the ideal-versus-measured input distinction.
They have been replaced by the two thin wrappers and one shared covariance
solver described above.

The desired-loss quantity remains a diagnostic. Its former guard was removed,
so it cannot reject a valid LCMV candidate or force uniform output. Safety
fallback still exists for missing/stale preserve evidence, unconfirmed jammer
activation, invalid/ill-conditioned covariance weights, non-finite weights,
excessive weight norm, or operator disable. There is intentionally no GUI switch
that disables all safety fallback.

## Why weight changes affect carrier and amplitude

For a desired spatial vector `a_B` and complex GNSS waveform `s[n]`, the
combined desired term is

```text
y_B[n] = w^H a_B s[n] = g_B s[n].
```

Changing from `w0` to `w1` changes the complex gain from `g0 = w0^H a_B` to
`g1 = w1^H a_B`. The output amplitude step is
`20 log10(|g1/g0|)` and its carrier-phase step is `arg(g1/g0)`. This follows
directly from the implemented conjugate weighted sum; it is not an inference
from C/N0.

The measured-bladeRF constraint sets the target response equal to the uniform
response. If both endpoints satisfy `a_B^H w = f`, every point in the complex
linear ramp also satisfies it:

```text
a_B^H ((1-alpha) w0 + alpha w1) = (1-alpha) f + alpha f = f.
```

Therefore uniform-response preservation is protective, not the cause of the
problem, when the frozen vector still matches the signal and calibration is
correct. It cannot reconstruct a signal after the finite bladeRF file ends, and
it can mismatch if the actual vector changes because hardware, multipath, or
front-end nonlinearity changes.

The code now uses a one-second complex ramp. Covariance updates cannot restart
or reverse an active ramp; a previous run did repeatedly update the target
before its ramp completed. Safety fallback and operator disable can still
preempt the ramp. The next marked run will compare weight progress against the
new carrier/code tracking archive for live proof.

## Suppression meaning

Generic output reduction versus uniform is not jammer-only suppression. It is
the change in total combined power between the LCMV weights and uniform weights,
and total power includes wanted bladeRF GNSS, real sky signals, jammer, noise,
multipath, and receiver artifacts. Wanted-signal loss can therefore make the
number look large.

The new estimator freezes the healthy arm-time covariance, forms the Hermitian
positive-semidefinite part of `R_current - R_arm`, and applies both the uniform
and actually applied weights to that same covariance. It reports

```text
10 log10((w_uniform^H R_excess w_uniform) /
         (w_applied^H R_excess w_applied)).
```

The automatic activation latch is only a two-threshold runtime safety gate. The
summary calls the covariance difference jammer-only only for samples inside an
explicit operator `jammer_on` marker while the bladeRF scene is otherwise
unchanged.

## Narrowband, phase, amplitude, and delay

The 4 MHz processed band at 1575.42 MHz has 0.254% fractional bandwidth, so the
single-frequency array steering approximation is reasonable. A scalar complex
calibration nevertheless corrects only one reference frequency. Differential
hardware delay leaves a phase slope `360 * Delta f * Delta tau` degrees.

The completed phase sweep measured the connected fixture-plus-receiver response
over +50 through +500 kHz. At -35 dBm, fitted channel-2 and channel-3 delays
relative to channel 0 were about 1.387 ns and 1.295 ns. Extrapolated to a
2 MHz edge those are about 0.999 and 0.932 degree, but the measurement did not
span the full +/-2 MHz operational band or prove the antenna-side chains. The
fixture/reference-plane evidence and the exact limits are documented in
`/home/tramiq_sdr/phase-calibration/docs/calibration_reference_planes_and_delay_2026-08-08.md`.
