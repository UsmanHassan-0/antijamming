from __future__ import annotations

import json

import numpy as np
import pytest

from antijamming.dsp.diagnostics import channel_power_metrics
from antijamming.dsp.phase import (
    apply_phase_calibration,
    load_calibration_correction_selection,
)


def _write_calibration(path, *, include_complex: bool = True) -> None:
    payload: dict[str, object] = {
        "quality_pass": True,
        "quality_max_phase_std_deg": 3.0,
        "phase_offsets_std_deg": [0.0, 0.1, 0.1, 0.1],
        "reference_channel": 0,
        "correction_vector": [
            {"real": 1.0, "imag": 0.0},
            {"real": 0.0, "imag": -1.0},
            {"real": -1.0, "imag": 0.0},
            {"real": 0.0, "imag": 1.0},
        ],
    }
    if include_complex:
        payload["complex_gain_phase_correction_vector"] = [
            {"real": 1.0, "imag": 0.0},
            {"real": 0.0, "imag": -2.0},
            {"real": -0.5, "imag": 0.0},
            {"real": 0.0, "imag": 1.5},
        ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_phase_only_mode_selects_phase_only_vector(tmp_path) -> None:
    path = tmp_path / "cal.json"
    _write_calibration(path)

    selection = load_calibration_correction_selection(
        path,
        mode="phase_only",
        expected_channel_count=4,
    )

    assert selection.applied_mode == "phase_only"
    assert selection.fallback_used is False
    assert np.allclose(np.abs(selection.vector), np.ones(4))


def test_complex_gain_mode_selects_complex_gain_vector(tmp_path) -> None:
    path = tmp_path / "cal.json"
    _write_calibration(path)

    selection = load_calibration_correction_selection(
        path,
        mode="complex_gain",
        expected_channel_count=4,
    )

    assert selection.applied_mode == "complex_gain"
    assert selection.fallback_used is False
    assert np.allclose(np.abs(selection.vector), [1.0, 2.0, 0.5, 1.5])


def test_omitted_mode_defaults_to_complex_gain_vector(tmp_path) -> None:
    path = tmp_path / "cal.json"
    _write_calibration(path)

    selection = load_calibration_correction_selection(
        path,
        expected_channel_count=4,
    )

    assert selection.configured_mode == "complex_gain"
    assert selection.applied_mode == "complex_gain"
    assert selection.fallback_used is False
    assert np.allclose(np.abs(selection.vector), [1.0, 2.0, 0.5, 1.5])


def test_complex_gain_missing_falls_back_to_phase_only(tmp_path) -> None:
    path = tmp_path / "cal.json"
    _write_calibration(path, include_complex=False)

    selection = load_calibration_correction_selection(
        path,
        mode="complex_gain",
        expected_channel_count=4,
    )

    assert selection.applied_mode == "phase_only"
    assert selection.fallback_used is True
    assert "complex_gain requested but invalid" in selection.fallback_reason
    assert np.allclose(np.abs(selection.vector), np.ones(4))


def test_complex_gain_correction_changes_calibrated_channel_power(tmp_path) -> None:
    path = tmp_path / "cal.json"
    _write_calibration(path)
    selection = load_calibration_correction_selection(
        path,
        mode="complex_gain",
        expected_channel_count=4,
    )
    raw = np.ones((4, 16), dtype=np.complex128)

    calibrated = apply_phase_calibration(raw, correction_vector=selection.vector)
    raw_metrics = channel_power_metrics(raw, prefix="raw")
    cal_metrics = channel_power_metrics(calibrated, prefix="cal")

    assert raw_metrics["raw_channel_powers_db"] == pytest.approx([0.0, 0.0, 0.0, 0.0])
    assert cal_metrics["cal_channel_powers_db"] != pytest.approx(
        raw_metrics["raw_channel_powers_db"]
    )
    assert cal_metrics["cal_ch1_power_db"] == pytest.approx(6.0206, rel=1e-4)
