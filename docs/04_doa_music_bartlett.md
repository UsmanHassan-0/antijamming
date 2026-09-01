# DoA MUSIC Bartlett

MUSIC estimates angle by decomposing the calibrated spatial covariance and scanning ideal steering vectors against the noise subspace. In this repo the implementation is under `src/antijamming/dsp/doa/music.py`; the backend logs MUSIC display and internal angles.

Bartlett angle-power is a conventional beam power scan. MUSIC can show a sharp pseudo-spectrum peak while Bartlett shows broader power or a different peak because they optimize different quantities. MUSIC depends heavily on source count and covariance eigenspace separation; Bartlett directly reports steered output power.

The realtime polar DoA graph plots only the current MUSIC pseudo-spectrum. It
converts the nonnegative raw MUSIC values to dB relative to that snapshot's
own maximum, maps the finite range to the polar radius, and draws a radial
marker at the primary MUSIC maximum. It does not plot absolute received power,
Bartlett, LCMV response, or a ground-truth jammer bearing. Bartlett is computed
from the same calibrated covariance and retained in the DoA/full-angle logs as
a diagnostic, but it is not a second curve in the realtime DoA graph.

Source-count diagnostics include:

- Largest eigenvalue gap
- Effective rank
- Noise-tail spread and flatness

The GUI displays configured source count plus eigen-gap and effective rank. Decimal values are allowed because effective rank is not an integer decision. AIC and MDL were removed because this four-channel live covariance repeatedly reported three sources during clean GNSS/multipath windows and the result was misleading the operator and baseline gate.
