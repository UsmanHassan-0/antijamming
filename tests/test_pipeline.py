from __future__ import annotations

import numpy as np

from antijamming.dsp.doa.music import (
    bartlett_spectrum,
    covariance_eigendecomposition,
    music_spectrum,
    source_count_diagnostics,
    steering_vector,
)
from antijamming.dsp.phase import correction_vector_from_phase_offsets_deg
from antijamming.dsp.pipeline import compute_realtime_metrics
from antijamming.dsp.pipeline.stages import (
    _dominant_spectrum_peaks,
    compute_doa_metrics,
)


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
        array_spacing_m=0.07,
        n_sources=1,
    )

    assert metrics["powers"].shape == (4,)
    assert metrics["phase_offsets_deg"].shape == (4,)
    assert metrics["phase_offsets_raw_deg"].shape == (4,)
    assert metrics["phase_offsets_calibrated_deg"].shape == (4,)
    assert metrics["doa_raw_spectrum"].shape == scan_angles.shape
    assert metrics["bartlett_raw_spectrum"].shape == scan_angles.shape
    assert "doa_spectrum" not in metrics
    assert metrics["covariance_eigenvalues"].shape == (4,)
    assert metrics["covariance_eigenvalues_db"].shape == (4,)
    assert metrics["covariance_eigen_gap_db"].shape == (3,)
    assert metrics["noise_tail_eigenvalues_rel_db"].shape == (3,)
    assert metrics["noise_tail_count"] == 3
    assert metrics["noise_tail_testable"] is True
    assert isinstance(metrics["noise_tail_white_like"], bool)
    assert np.isfinite(metrics["noise_tail_spread_db"])
    assert np.isfinite(metrics["noise_tail_flatness_db"])
    assert "source_estimate_mdl" not in metrics
    assert "source_estimate_aic" not in metrics
    assert 0.0 <= metrics["doa_deg"] <= 359.0
    assert np.isfinite(metrics["doa_raw_spectrum"]).all()
    assert np.min(metrics["doa_raw_spectrum"]) >= -1e-9
    assert np.isfinite(metrics["bartlett_raw_spectrum"]).all()
    assert np.min(metrics["bartlett_raw_spectrum"]) >= -1e-9
    assert isinstance(metrics["doa_peaks"], list)
    assert metrics["doa_peak_count"] == len(metrics["doa_peaks"])
    assert metrics["doa_peak_count"] >= 1
    assert isinstance(metrics["bartlett_peaks"], list)
    assert metrics["bartlett_peak_count"] == len(metrics["bartlett_peaks"])


def test_doa_shared_covariance_matches_independent_reference_formulas() -> None:
    """One shared covariance must not change MUSIC, Bartlett, or eigenvalues."""

    rng = np.random.default_rng(20260816)
    samples = (
        rng.standard_normal((4, 4096))
        + 1j * rng.standard_normal((4, 4096))
    ).astype(np.complex128)
    scan_angles = np.linspace(0.0, 359.0, 720)

    actual = compute_doa_metrics(
        samples,
        center_freq_hz=1.57542e9,
        scan_angles_deg=scan_angles,
        array_spacing_m=0.095,
        n_sources=2,
    )
    expected_music = music_spectrum(
        samples,
        1.57542e9,
        scan_angles,
        0.095,
        n_sources=2,
        normalize=False,
    )
    expected_bartlett = bartlett_spectrum(
        samples,
        1.57542e9,
        scan_angles,
        0.095,
        normalize=False,
    )
    expected_eigenvalues, expected_eigenvectors = covariance_eigendecomposition(
        samples
    )

    np.testing.assert_allclose(actual["doa_raw_spectrum"], expected_music, rtol=1e-13)
    np.testing.assert_allclose(
        actual["bartlett_raw_spectrum"],
        expected_bartlett,
        rtol=1e-13,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        actual["covariance_eigenvalues"],
        expected_eigenvalues,
        rtol=1e-14,
    )
    np.testing.assert_allclose(
        np.abs(actual["covariance_eigenvectors"]),
        np.abs(expected_eigenvectors),
        rtol=1e-14,
        atol=1e-15,
    )


def test_dominant_spectrum_peaks_reports_separated_music_peaks() -> None:
    scan_angles = np.linspace(0.0, 359.0, 360)
    spectrum = np.full(scan_angles.shape, 0.05, dtype=np.float64)
    spectrum[30] = 1.0
    spectrum[160] = 0.62
    spectrum[31] = 0.7
    spectrum[159] = 0.44

    peaks = _dominant_spectrum_peaks(
        scan_angles,
        spectrum,
        max_reported_peaks=4,
        min_peak_height=0.25,
        min_separation_deg=8.0,
    )

    assert [round(peak["angle_deg"]) for peak in peaks[:2]] == [30, 160]
    assert peaks[0]["height"] == 1.0
    assert peaks[1]["height"] == 0.62


def test_source_count_diagnostics_reports_single_dominant_source() -> None:
    rng = np.random.default_rng(123)
    sample_count = 4096
    signal = rng.standard_normal(sample_count) + 1j * rng.standard_normal(sample_count)
    signature = steering_vector(
        np.array([72.0], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)
    noise = 0.01 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    x = signature[:, None] * signal[None, :] + noise

    diagnostics = source_count_diagnostics(x)

    assert np.asarray(diagnostics["covariance_eigenvalues"]).shape == (4,)
    assert np.asarray(diagnostics["covariance_eigen_gap_db"])[0] > 30.0
    assert int(diagnostics["noise_tail_count"]) == 3
    assert bool(diagnostics["noise_tail_testable"]) is True
    assert bool(diagnostics["noise_tail_white_like"]) is True
    assert float(diagnostics["noise_tail_spread_db"]) <= float(
        diagnostics["noise_tail_spread_threshold_db"]
    )


def test_source_count_diagnostics_flags_colored_noise_tail() -> None:
    rng = np.random.default_rng(456)
    sample_count = 4096
    scales = np.array([1.0, 1.0, 0.25, 0.05], dtype=np.float64)
    x = scales[:, None] * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )

    diagnostics = source_count_diagnostics(x, noise_tail_sources=1)

    assert int(diagnostics["noise_tail_count"]) == 3
    assert bool(diagnostics["noise_tail_testable"]) is True
    assert bool(diagnostics["noise_tail_white_like"]) is False
    assert float(diagnostics["noise_tail_spread_db"]) > float(
        diagnostics["noise_tail_spread_threshold_db"]
    )


def test_bartlett_spectrum_reports_conventional_beamformer_peak() -> None:
    rng = np.random.default_rng(321)
    sample_count = 2048
    source_angle_deg = 72.0
    signal = rng.standard_normal(sample_count) + 1j * rng.standard_normal(sample_count)
    signature = steering_vector(
        np.array([source_angle_deg], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)
    x = signature[:, None] * signal[None, :]
    scan_angles = np.linspace(0.0, 359.0, 360)

    spectrum = bartlett_spectrum(
        x,
        1.57542e9,
        scan_angles,
        0.07,
        normalize=False,
    )
    peak_angle = float(scan_angles[int(np.argmax(spectrum))])
    angle_error = abs((peak_angle - source_angle_deg + 180.0) % 360.0 - 180.0)

    assert angle_error <= 1.0
    assert np.isfinite(spectrum).all()
    assert np.max(spectrum) > np.median(spectrum)


def test_four_channel_steering_matches_physical_square_mapping() -> None:
    rf_freq_hz = 1.57542e9
    k = 2.0 * np.pi * rf_freq_hz / 299792458.0
    half_spacing = 0.035

    vectors = steering_vector(
        np.array([0.0, 90.0], dtype=np.float64),
        rf_freq_hz,
        0.07,
    )

    az0 = vectors[:, 0]
    az90 = vectors[:, 1]
    assert np.isclose(az0[0], az0[1])
    assert np.isclose(az0[2], az0[3])
    assert np.isclose(az0[2] / az0[0], np.exp(1j * k * 2.0 * half_spacing))
    assert np.isclose(az90[0], az90[3])
    assert np.isclose(az90[1], az90[2])
    assert np.isclose(az90[1] / az90[0], np.exp(1j * k * 2.0 * half_spacing))


def test_compute_realtime_metrics_clamps_music_sources_to_noise_subspace() -> None:
    rng = np.random.default_rng(7)
    buffer = (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    ).astype(np.complex128)
    scan_angles = np.linspace(0.0, 359.0, 721)

    metrics = compute_realtime_metrics(
        buffer=buffer,
        center_freq_hz=1.57542e9,
        scan_angles_deg=scan_angles,
        array_spacing_m=0.07,
        n_sources=99,
    )

    assert metrics["n_sources"] == 3
    assert np.isfinite(metrics["doa_raw_spectrum"]).all()
    assert np.isfinite(metrics["bartlett_raw_spectrum"]).all()
    assert "doa_spectrum" not in metrics


def test_compute_realtime_metrics_leaves_phase_uncorrected_without_static_calibration() -> None:
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
        array_spacing_m=0.07,
        n_sources=1,
    )

    assert np.max(np.abs(metrics["phase_offsets_raw_deg"][1:])) > 20.0
    assert np.allclose(
        metrics["phase_offsets_calibrated_deg"],
        metrics["phase_offsets_raw_deg"],
        atol=1e-9,
    )


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
        array_spacing_m=0.07,
        n_sources=1,
        phase_correction_vector=correction_vector_from_phase_offsets_deg(offsets_deg),
    )

    assert np.max(np.abs(metrics["phase_offsets_calibrated_deg"][1:])) < 1e-6
