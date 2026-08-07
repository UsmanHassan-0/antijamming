# Known Failure Modes

- Ideal null deep but U1 suppression weak: the ideal model angle may not match the measured dominant spatial vector.
- U1 is not always jammer: with jammer off, U1 can be desired/SOI, bladeRF-like baseline, multipath, or receiver artifact.
- SOI included in covariance: full covariance LCMV can suppress useful signal if the desired constraint is wrong or missing.
- Ideal desired steering does not equal the OTA bladeRF vector: observed live on 2026-08-07 as a 7-9 dB C/N0 loss with jammer off even though the ideal preserve residual was approximately zero. The product now freezes measured bladeRF U1 instead.
- False activation from angle motion: angle jumps and covariance-vector changes can occur without the jammer. The product gate therefore requires a positive input-power rise plus generalized covariance gain; angle alone is forbidden.
- Abrupt or continually changing weights: one-chunk changes can disturb GNSS carrier/code tracking. Product weights now use a logged one-second complex ramp.
- Noise gain due to large weights: high `||w||^2` can make the receiver noisier even if a null looks deep.
- Total output reduction misleading: total output includes desired, jammer, sky GNSS, noise, multipath, and artifacts.
- Stale RF metadata: J/S conclusions are only as good as the logged RF budget and attenuation basis.
- Heavy logging stalls: diagnostics should remain throttled during long realtime runs.
- Missing operator markers: inferred `jammer_like_event` labels do not prove that the physical jammer was switched on.
- Complex gain not equal OTA calibration: splitter calibration corrects chains, not the full antenna manifold.
- Legacy wide nulls consume DOF: the old angle-fan helper is not a live product candidate.
- Internal/display angle confusion: display 170 is internal 280; steering vectors must use internal angles.
- Duplicate USRP owners: two backends can both hold UHD sessions and interleave the same log files. Confirm one active backend/X300 owner before treating a live run as authoritative.
