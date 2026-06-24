"""Direction-of-arrival steering vectors and MUSIC spectrum estimation."""

from __future__ import annotations

import numpy as np


# =============================================================================
# Array Steering Model
# =============================================================================

# The array model used throughout the realtime GUI is a uniform circular array.
# Angles are expressed in degrees and follow the same azimuth convention used by
# the DoA and beamforming plots: 0..359 degrees over one full scan.

def steering_vector(
    theta_deg: np.ndarray,
    rf_freq_hz: float,
    n_channels: int,
    radius_m: float,
) -> np.ndarray:
    """Return UCA steering vectors for the requested scan angles."""
    # Keep the speed of light local to the steering model so callers only need
    # RF frequency and physical radius; array spacing conversions happen in config.
    c0 = 299792458.0
    k = 2.0 * np.pi * rf_freq_hz / c0
    theta = np.deg2rad(theta_deg)
    phi = 2.0 * np.pi * np.arange(n_channels, dtype=np.float64) / max(n_channels, 1)
    phase = -1j * k * radius_m * np.cos(phi[:, None] - theta[None, :])
    return np.exp(phase)


# =============================================================================
# Direction-of-Arrival Spectra
# =============================================================================

# MUSIC is the only DoA estimator exposed by the runtime. The output is
# normalized for plotting, not calibrated as an absolute power estimate.

def music_spectrum(
    x: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    uca_radius_m: float,
    n_sources: int = 1,
    normalize: bool = True,
) -> np.ndarray:
    """Estimate the MUSIC pseudo-spectrum for a channel snapshot matrix."""
    x = np.asarray(x, dtype=np.complex128)
    # x is shaped [channels, samples]. The covariance is intentionally estimated
    # from the current processing chunk so the GUI reflects live array state.
    r = (x @ x.conj().T) / max(x.shape[1], 1)
    evals, evecs = np.linalg.eigh(r)
    order = np.argsort(evals)[::-1]
    evecs = evecs[:, order]
    # The largest eigenvectors are treated as the signal subspace; the remaining
    # vectors form the noise subspace used by the MUSIC denominator.
    n_noise = max(x.shape[0] - n_sources, 1)
    en = evecs[:, n_sources : n_sources + n_noise]
    a = steering_vector(scan_angles_deg, rf_freq_hz, x.shape[0], uca_radius_m)
    # Vectorized MUSIC: denom[k] = a_k^H (En En^H) a_k
    proj = en @ en.conj().T  # (M, M)
    pa = proj @ a  # (M, K)
    denom = np.real(np.sum(a.conj() * pa, axis=0)) + 1e-12  # (K,)
    p = 1.0 / denom
    if not normalize:
        return p
    return p / (np.max(p) + 1e-12)
