# Known Failure Modes

- Ideal null deep but U1 suppression weak: the ideal model angle may not match the measured dominant spatial vector.
- U1 is not always jammer: with jammer off, U1 can be desired/SOI, bladeRF-like baseline, multipath, or receiver artifact.
- SOI included in covariance: full covariance LCMV can suppress useful signal if the desired constraint is wrong or missing.
- LCMV suppresses desired when jammer off: measured-U1 methods are especially risky when jammer confidence is low.
- Noise gain due to large weights: high `||w||^2` can make the receiver noisier even if a null looks deep.
- Total output reduction misleading: total output includes desired, jammer, sky GNSS, noise, multipath, and artifacts.
- Stale RF metadata: J/S conclusions are only as good as the logged RF budget and attenuation basis.
- Heavy logging stalls: diagnostics should remain throttled during long realtime runs.
- Missing operator markers: inferred `jammer_like_event` labels do not prove that the physical jammer was switched on.
- Complex gain not equal OTA calibration: splitter calibration corrects chains, not the full antenna manifold.
- Legacy wide nulls consume DOF: the old angle-fan helper is not a live product candidate.
- Internal/display angle confusion: display 170 is internal 280; steering vectors must use internal angles.
