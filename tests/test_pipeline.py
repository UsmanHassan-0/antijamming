from __future__ import annotations

import numpy as np

from antijamming.dsp.phase import correction_vector_from_phase_offsets_deg
from antijamming.dsp.pipeline import compute_realtime_metrics


def test_compute_realtime_metrics_shapes_and_ranges() -> None:
    rng = np.random.default_rng(42)
    buffer = (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    ).astype(np.complex128)
    scan_angles = np.linspace(0.0, 359.0, 721)

    metrics = compute_realtime_metrics(
        buffer=buffer,
        center_freq_hz=1.585e9,
        scan_angles_deg=scan_angles,
        uca_radius_m=0.0669,
        ref_channel=0,
        n_sources=1,
        doa_method="music",
    )

    assert metrics["powers"].shape == (4,)
    assert metrics["phase_offsets_deg"].shape == (4,)
    assert metrics["phase_offsets_raw_deg"].shape == (4,)
    assert metrics["phase_offsets_calibrated_deg"].shape == (4,)
    assert metrics["music_spectrum"].shape == scan_angles.shape
    assert metrics["lcmv_pattern_db"].shape == scan_angles.shape
    assert 0.0 <= metrics["doa_deg"] <= 359.0
    assert np.max(metrics["music_spectrum"]) <= 1.000001
    assert np.min(metrics["music_spectrum"]) >= -1e-9


def test_compute_realtime_metrics_reports_phase_after_calibration() -> None:
    sample_count = 256
    t = np.linspace(0.0, 1.0, sample_count, endpoint=False)
    ref = np.exp(1j * 2.0 * np.pi * 3.0 * t)
    buffer = np.vstack(
        [
            ref,
            ref * np.exp(1j * np.deg2rad(30.0)),
            ref * np.exp(1j * np.deg2rad(-55.0)),
            ref * np.exp(1j * np.deg2rad(80.0)),
        ]
    ).astype(np.complex128)
    scan_angles = np.linspace(0.0, 359.0, 721)

    metrics = compute_realtime_metrics(
        buffer=buffer,
        center_freq_hz=1.585e9,
        scan_angles_deg=scan_angles,
        uca_radius_m=0.0669,
        ref_channel=0,
        n_sources=1,
        doa_method="music",
    )

    assert np.max(np.abs(metrics["phase_offsets_raw_deg"][1:])) > 20.0
    assert np.max(np.abs(metrics["phase_offsets_calibrated_deg"][1:])) < 1e-6


def test_compute_realtime_metrics_accepts_static_phase_correction() -> None:
    sample_count = 256
    t = np.linspace(0.0, 1.0, sample_count, endpoint=False)
    ref = np.exp(1j * 2.0 * np.pi * 3.0 * t)
    offsets_deg = np.array([0.0, 25.0, -35.0, 70.0], dtype=np.float64)
    buffer = np.vstack(
        [ref * np.exp(1j * np.deg2rad(offset)) for offset in offsets_deg]
    ).astype(np.complex128)
    scan_angles = np.linspace(0.0, 359.0, 721)

    metrics = compute_realtime_metrics(
        buffer=buffer,
        center_freq_hz=1.585e9,
        scan_angles_deg=scan_angles,
        uca_radius_m=0.0669,
        ref_channel=0,
        n_sources=1,
        doa_method="music",
        phase_correction_vector=correction_vector_from_phase_offsets_deg(offsets_deg),
    )

    assert np.max(np.abs(metrics["phase_offsets_calibrated_deg"][1:])) < 1e-6
