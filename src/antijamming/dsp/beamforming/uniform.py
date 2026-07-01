"""Uniform all-channel IQ combiner helpers."""

from __future__ import annotations

import numpy as np


def uniform_weights(n_channels: int) -> np.ndarray:
    """Return a unity-gain uniform spatial average for the channel count."""
    n = max(int(n_channels), 1)
    return np.full((n,), 1.0 / float(n), dtype=np.complex128)


def apply_beamformer(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Apply complex channel weights to a channel-by-sample matrix."""
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
