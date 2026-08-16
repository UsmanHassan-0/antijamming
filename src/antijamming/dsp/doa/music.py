"""Direction-of-arrival steering vectors and MUSIC spectrum estimation."""

from __future__ import annotations

import numpy as np


# =============================================================================
# Array Steering Model
# =============================================================================

# The live 4-channel array is wired as a square URA:
#   ch1/ant2  ch2/ant3
#   ch0/ant1  ch3/ant4
# Internal azimuth uses +x for ch0->ch3 and +y for ch0->ch1.

def _array_xy_positions_m(array_spacing_m: float) -> np.ndarray:
    """Return the fixed 4-element square array x/y coordinates."""

    spacing = float(array_spacing_m)
    half_spacing = spacing / 2.0
    return np.asarray(
        [
            [-half_spacing, -half_spacing],  # ch0 / ant1
            [-half_spacing, +half_spacing],  # ch1 / ant2
            [+half_spacing, +half_spacing],  # ch2 / ant3
            [+half_spacing, -half_spacing],  # ch3 / ant4
        ],
        dtype=np.float64,
    )

def steering_vector(
    theta_deg: np.ndarray,
    rf_freq_hz: float,
    array_spacing_m: float,
) -> np.ndarray:
    """Return planar steering vectors for the requested scan angles."""
    c0 = 299792458.0
    k = 2.0 * np.pi * rf_freq_hz / c0
    theta = np.deg2rad(theta_deg)
    positions = _array_xy_positions_m(array_spacing_m)
    direction = np.vstack((np.cos(theta), np.sin(theta)))
    phase = 1j * k * (positions @ direction)
    return np.exp(phase)


# =============================================================================
# Covariance Diagnostics
# =============================================================================

# These helpers run before MUSIC so the runtime can inspect the live spatial
# rank instead of blindly trusting the operator-selected source count.

NOISE_TAIL_SPREAD_WHITE_LIKE_DB = 3.0
NOISE_TAIL_FLATNESS_WHITE_LIKE_DB = 1.0

def spatial_covariance(x: np.ndarray) -> np.ndarray:
    """Return the channel covariance matrix for [channels, samples] IQ data."""

    x = np.asarray(x, dtype=np.complex128)
    if x.ndim != 2 or x.shape[0] == 0:
        return np.zeros((0, 0), dtype=np.complex128)
    return (x @ x.conj().T) / max(int(x.shape[1]), 1)


def covariance_eigendecomposition(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return covariance eigenvalues/eigenvectors sorted strongest first."""

    return covariance_eigendecomposition_from_matrix(spatial_covariance(x))


def covariance_eigendecomposition_from_matrix(
    covariance: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Diagonalize an already-computed covariance matrix strongest first."""

    r = np.asarray(covariance, dtype=np.complex128)
    if r.size == 0:
        return (
            np.zeros((0,), dtype=np.float64),
            np.zeros((0, 0), dtype=np.complex128),
        )
    evals, evecs = np.linalg.eigh(r)
    order = np.argsort(evals)[::-1]
    return np.asarray(np.real(evals[order]), dtype=np.float64), evecs[:, order]


def _noise_tail_diagnostics(
    evals_desc: np.ndarray,
    *,
    source_count: int,
) -> dict[str, object]:
    """Return flatness checks for the eigenvalue tail treated as noise."""

    eig = np.maximum(np.asarray(evals_desc, dtype=np.float64).reshape(-1), 1e-30)
    m = int(eig.size)
    if m <= 0:
        return {
            "noise_tail_assumed_sources": 0,
            "noise_tail_count": 0,
            "noise_tail_eigenvalues_rel_db": np.zeros((0,), dtype=np.float64),
            "noise_tail_spread_db": 0.0,
            "noise_tail_flatness_db": 0.0,
            "noise_tail_testable": False,
            "noise_tail_white_like": False,
            "noise_tail_spread_threshold_db": NOISE_TAIL_SPREAD_WHITE_LIKE_DB,
            "noise_tail_flatness_threshold_db": NOISE_TAIL_FLATNESS_WHITE_LIKE_DB,
        }

    k = min(max(int(source_count), 0), max(m - 1, 0))
    tail = eig[k:]
    tail_count = int(tail.size)
    tail_db = 10.0 * np.log10(tail)
    tail_rel_db = tail_db - float(tail_db[0]) if tail_count else tail_db
    if tail_count >= 2:
        spread_db = float(np.max(tail_db) - np.min(tail_db))
        log_gm = float(np.mean(np.log(tail)))
        am = float(np.mean(tail))
        flatness_db = float(10.0 * np.log10(max(am, 1e-30)) - (10.0 / np.log(10.0)) * log_gm)
        white_like = (
            spread_db <= NOISE_TAIL_SPREAD_WHITE_LIKE_DB
            and flatness_db <= NOISE_TAIL_FLATNESS_WHITE_LIKE_DB
        )
        testable = True
    else:
        spread_db = 0.0
        flatness_db = 0.0
        white_like = False
        testable = False

    return {
        "noise_tail_assumed_sources": k,
        "noise_tail_count": tail_count,
        "noise_tail_eigenvalues_rel_db": np.asarray(tail_rel_db, dtype=np.float64),
        "noise_tail_spread_db": spread_db,
        "noise_tail_flatness_db": flatness_db,
        "noise_tail_testable": testable,
        "noise_tail_white_like": bool(white_like),
        "noise_tail_spread_threshold_db": NOISE_TAIL_SPREAD_WHITE_LIKE_DB,
        "noise_tail_flatness_threshold_db": NOISE_TAIL_FLATNESS_WHITE_LIKE_DB,
    }


def source_count_diagnostics(
    x: np.ndarray,
    noise_tail_sources: int = 1,
) -> dict[str, object]:
    """Estimate live spatial rank before MUSIC selects a noise subspace."""

    evals, _ = covariance_eigendecomposition(x)
    return source_count_diagnostics_from_eigenvalues(
        evals,
        noise_tail_sources=noise_tail_sources,
    )


def source_count_diagnostics_from_eigenvalues(
    eigenvalues: np.ndarray,
    noise_tail_sources: int = 1,
) -> dict[str, object]:
    """Estimate spatial rank from a previously computed eigendecomposition."""

    evals_safe = np.maximum(
        np.asarray(eigenvalues, dtype=np.float64).reshape(-1),
        1e-30,
    )
    if evals_safe.size == 0:
        return {
            "covariance_eigenvalues": evals_safe,
            "covariance_eigenvalues_db": np.zeros((0,), dtype=np.float64),
            "covariance_eigenvalues_rel_db": np.zeros((0,), dtype=np.float64),
            "covariance_eigen_gap_db": np.zeros((0,), dtype=np.float64),
            "source_estimate_gap": 0,
            "source_effective_rank": 0.0,
            **_noise_tail_diagnostics(evals_safe, source_count=noise_tail_sources),
        }

    eig_db = 10.0 * np.log10(evals_safe)
    eig_rel_db = eig_db - float(eig_db[0])
    if evals_safe.size > 1:
        gap_db = 10.0 * np.log10(evals_safe[:-1] / evals_safe[1:])
        finite_gap = np.where(np.isfinite(gap_db), gap_db, -np.inf)
        source_estimate_gap = int(np.argmax(finite_gap) + 1)
    else:
        gap_db = np.zeros((0,), dtype=np.float64)
        source_estimate_gap = 0
    weights = evals_safe / max(float(np.sum(evals_safe)), 1e-30)
    effective_rank = float(
        np.exp(-np.sum(weights * np.log(np.maximum(weights, 1e-30))))
    )
    return {
        "covariance_eigenvalues": evals_safe,
        "covariance_eigenvalues_db": np.asarray(eig_db, dtype=np.float64),
        "covariance_eigenvalues_rel_db": np.asarray(eig_rel_db, dtype=np.float64),
        "covariance_eigen_gap_db": np.asarray(gap_db, dtype=np.float64),
        "source_estimate_gap": source_estimate_gap,
        "source_effective_rank": effective_rank,
        **_noise_tail_diagnostics(evals_safe, source_count=noise_tail_sources),
    }


# =============================================================================
# Direction-of-Arrival Spectra
# =============================================================================

# MUSIC remains the DoA estimator exposed by the runtime. Bartlett is logged as
# a conventional beamformer diagnostic so MUSIC's pseudo-spectrum can be
# compared against an angle-by-angle output-power estimate.

def bartlett_spectrum(
    x: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    normalize: bool = True,
) -> np.ndarray:
    """Estimate the Bartlett/conventional beamformer power over scan angles."""
    x = np.asarray(x, dtype=np.complex128)
    n_channels = int(x.shape[0])
    if n_channels != 4:
        raise ValueError(
            f"Bartlett steering model expects the fixed 4-channel array, got {n_channels}"
        )
    return bartlett_spectrum_from_covariance(
        spatial_covariance(x),
        rf_freq_hz,
        scan_angles_deg,
        array_spacing_m,
        normalize=normalize,
    )


def bartlett_spectrum_from_covariance(
    covariance: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    normalize: bool = True,
) -> np.ndarray:
    """Evaluate Bartlett using an already-computed channel covariance."""

    r = np.asarray(covariance, dtype=np.complex128)
    if r.shape != (4, 4):
        raise ValueError(
            f"Bartlett steering model expects a 4x4 covariance, got {r.shape}"
        )
    a = steering_vector(scan_angles_deg, rf_freq_hz, array_spacing_m)
    ra = r @ a
    numerator = np.real(np.sum(a.conj() * ra, axis=0))
    denominator = np.maximum(np.sum(np.abs(a) ** 2, axis=0) ** 2, 1e-30)
    p = np.maximum(numerator / denominator, 0.0)
    if not normalize:
        return p
    return p / (np.max(p) + 1e-12)

def music_spectrum(
    x: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    n_sources: int = 1,
    normalize: bool = True,
) -> np.ndarray:
    """Estimate the MUSIC pseudo-spectrum for a channel snapshot matrix."""
    x = np.asarray(x, dtype=np.complex128)
    n_channels = int(x.shape[0])
    if n_channels != 4:
        raise ValueError(
            f"MUSIC steering model expects the fixed 4-channel array, got {n_channels}"
        )
    # x is shaped [channels, samples]. The covariance is intentionally estimated
    # from the current processing chunk so the GUI reflects live array state.
    _, evecs = covariance_eigendecomposition(x)
    return music_spectrum_from_eigenvectors(
        evecs,
        rf_freq_hz,
        scan_angles_deg,
        array_spacing_m,
        n_sources=n_sources,
        normalize=normalize,
    )


def music_spectrum_from_eigenvectors(
    eigenvectors: np.ndarray,
    rf_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    n_sources: int = 1,
    normalize: bool = True,
) -> np.ndarray:
    """Evaluate MUSIC using previously computed covariance eigenvectors."""

    evecs = np.asarray(eigenvectors, dtype=np.complex128)
    if evecs.shape != (4, 4):
        raise ValueError(
            f"MUSIC steering model expects 4x4 eigenvectors, got {evecs.shape}"
        )
    n_channels = int(evecs.shape[0])
    n_sources = min(max(int(n_sources), 1), max(n_channels - 1, 1))
    # The largest eigenvectors are treated as the signal subspace; the remaining
    # vectors form the noise subspace used by the MUSIC denominator.
    n_noise = max(n_channels - n_sources, 1)
    en = evecs[:, n_sources : n_sources + n_noise]
    a = steering_vector(scan_angles_deg, rf_freq_hz, array_spacing_m)
    # Vectorized MUSIC: denom[k] = a_k^H (En En^H) a_k
    proj = en @ en.conj().T  # (M, M)
    pa = proj @ a  # (M, K)
    denom = np.real(np.sum(a.conj() * pa, axis=0)) + 1e-12  # (K,)
    p = 1.0 / denom
    if not normalize:
        return p
    return p / (np.max(p) + 1e-12)
