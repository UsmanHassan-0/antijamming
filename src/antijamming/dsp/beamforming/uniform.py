"""Uniform all-channel IQ combiner helpers."""

from __future__ import annotations

import numpy as np


def uniform_weights(n_channels: int) -> np.ndarray:
    """Return equal raw-sum weights for the channel count."""
    n = max(int(n_channels), 1)
    return np.ones((n,), dtype=np.complex128)


def apply_beamformer(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Apply complex combiner weights to a channel-by-sample matrix."""
    samples = np.asarray(x, dtype=np.complex128)
    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    if samples.ndim != 2 or samples.shape[0] == 0 or w.size == 0:
        return np.zeros((0,), dtype=np.complex64)
    if samples.shape[0] != w.size:
        raise ValueError(
            f"combiner weight count {w.size} does not match channel count {samples.shape[0]}"
        )
    y = w.conj() @ samples
    return np.asarray(y, dtype=np.complex64)
