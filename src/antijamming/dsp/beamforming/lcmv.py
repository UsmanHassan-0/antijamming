"""LCMV beamforming weights, application, and response display helpers."""

from __future__ import annotations

import numpy as np

from antijamming.dsp.doa.music import steering_vector


# =============================================================================
# Beamformer Weight Solvers
# =============================================================================

# Beamforming helpers are pure numerical functions. Runtime decisions such as
# whether nulling is enabled or safe belong in the backend, not here.

def uniform_weights(n_channels: int) -> np.ndarray:
    """Return a unity-gain uniform spatial average for the channel count."""
    n = max(int(n_channels), 1)
    return np.full((n,), 1.0 / float(n), dtype=np.complex128)


def look_direction_weights(
    theta_deg: float,
    rf_freq_hz: float,
    n_channels: int,
    uca_radius_m: float,
) -> np.ndarray:
    """Return unity-gain delay-and-sum weights steered to one look direction."""

    n = max(int(n_channels), 1)
    a_look = steering_vector(
        np.array([float(theta_deg)], dtype=np.float64),
        rf_freq_hz,
        n,
        uca_radius_m,
    ).reshape(-1)
    return np.asarray(a_look / float(n), dtype=np.complex128)


def dominant_spatial_signature(x: np.ndarray) -> np.ndarray:
    """Estimate the strongest live spatial signature from channel covariance."""
    x = np.asarray(x, dtype=np.complex128)
    if x.ndim != 2 or x.shape[0] == 0 or x.shape[1] == 0:
        return np.zeros((0,), dtype=np.complex128)
    r = (x @ x.conj().T) / max(x.shape[1], 1)
    evals, evecs = np.linalg.eigh(r)
    vector = np.asarray(evecs[:, int(np.argmax(evals))], dtype=np.complex128)
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0 or not np.isfinite(norm):
        return np.zeros((0,), dtype=np.complex128)
    return vector * (np.sqrt(float(x.shape[0])) / norm)


def lcmv_weights(
    x: np.ndarray,
    rf_freq_hz: float,
    null_theta_deg: float,
    uca_radius_m: float,
    diagonal_loading: float = 1e-3,
    ref_channel: int | None = None,
    null_vector: np.ndarray | None = None,
) -> np.ndarray:
    """Solve LCMV weights with one pass constraint and one spatial null."""
    x = np.asarray(x, dtype=np.complex128)
    if x.ndim != 2 or x.shape[0] == 0:
        return np.zeros((0,), dtype=np.complex128)
    # Diagonal loading keeps the covariance inversion usable for short chunks,
    # correlated inputs, or nearly singular lab captures.
    r = (x @ x.conj().T) / max(x.shape[1], 1)
    r_loaded = r + diagonal_loading * np.eye(r.shape[0], dtype=np.complex128)
    if null_vector is None:
        a_null = steering_vector(
            np.array([null_theta_deg], dtype=np.float64),
            rf_freq_hz,
            x.shape[0],
            uca_radius_m,
        )
    else:
        candidate = np.asarray(null_vector, dtype=np.complex128).reshape(-1)
        candidate_norm = float(np.linalg.norm(candidate))
        if candidate.size == x.shape[0] and candidate_norm > 0.0 and np.isfinite(candidate_norm):
            a_null = (
                candidate * (np.sqrt(float(x.shape[0])) / candidate_norm)
            ).reshape(-1, 1)
        else:
            a_null = steering_vector(
                np.array([null_theta_deg], dtype=np.float64),
                rf_freq_hz,
                x.shape[0],
                uca_radius_m,
            )
    # Constraint matrix: preserve either the configured GNSS reference channel
    # or the legacy broad pass response while forcing a null at the jammer DoA.
    if ref_channel is None:
        a_pass = np.ones((x.shape[0], 1), dtype=np.complex128)
    else:
        channel = max(0, min(int(ref_channel), x.shape[0] - 1))
        a_pass = np.zeros((x.shape[0], 1), dtype=np.complex128)
        a_pass[channel, 0] = 1.0 + 0.0j
    c = np.hstack([a_pass, a_null])
    f = np.array([[1.0 + 0.0j], [0.0 + 0.0j]], dtype=np.complex128)
    r_inv = np.linalg.pinv(r_loaded)
    mid = np.linalg.pinv(c.conj().T @ r_inv @ c)
    return (r_inv @ c @ mid @ f).reshape(-1)


# =============================================================================
# Beamformer Application and Display Response
# =============================================================================

# apply_beamformer returns one complex stream for GNSS-SDR handoff. Pattern
# helpers below are display metrics only and should not be fed back into SDR I/O.

def apply_beamformer(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Apply complex beamforming weights to a channel-by-sample matrix."""
    x = np.asarray(x, dtype=np.complex128)
    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    if x.ndim != 2 or x.shape[0] == 0 or w.size == 0:
        return np.zeros((0,), dtype=np.complex64)
    if x.shape[0] != w.size:
        raise ValueError(
            f"beamformer weight count {w.size} does not match channel count {x.shape[0]}"
        )
    y = w.conj() @ x
    return np.asarray(y, dtype=np.complex64)


def lcmv_pattern_db(
    x: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    null_theta_deg: float,
    uca_radius_m: float,
    diagonal_loading: float = 1e-3,
    ref_channel: int | None = None,
    null_vector: np.ndarray | None = None,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """Return the normalized LCMV array response in dB for display."""
    x = np.asarray(x, dtype=np.complex128)
    if weights is None:
        w = lcmv_weights(
            x=x,
            rf_freq_hz=rf_freq_hz,
            null_theta_deg=null_theta_deg,
            uca_radius_m=uca_radius_m,
            diagonal_loading=diagonal_loading,
            ref_channel=ref_channel,
            null_vector=null_vector,
        )
    else:
        w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    if w.size == 0:
        return np.zeros_like(scan_angles_deg, dtype=np.float64)
    # Normalize by the maximum response so plot limits stay stable across gains
    # and sample amplitudes; null depth remains visible in dB.
    a_scan = steering_vector(scan_angles_deg, rf_freq_hz, x.shape[0], uca_radius_m)
    resp = np.abs(w.conj().T @ a_scan) ** 2
    resp = resp / (np.max(resp) + 1e-12)
    return 10.0 * np.log10(resp + 1e-12)


def beamformer_pattern_db(
    weights: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    n_channels: int,
    uca_radius_m: float,
) -> np.ndarray:
    """Return the normalized array response for already-selected weights."""

    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    if w.size == 0:
        return np.zeros_like(scan_angles_deg, dtype=np.float64)
    a_scan = steering_vector(scan_angles_deg, rf_freq_hz, int(n_channels), uca_radius_m)
    resp = np.abs(w.conj().T @ a_scan) ** 2
    resp = resp / (np.max(resp) + 1e-12)
    return 10.0 * np.log10(resp + 1e-12)
