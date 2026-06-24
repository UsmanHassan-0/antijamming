from __future__ import annotations

import logging

import numpy as np
import pytest

from antijamming.runtime import BackendRuntime
from antijamming.detection.jammer import JammerDetector, JammerDetectorConfig
from antijamming.config import StreamConfig


def _runtime(config: StreamConfig | None = None) -> BackendRuntime:
    loggers = {
        name: logging.getLogger(f"test_jammer_detection.{name}")
        for name in ["app", "hw", "stream", "transport", "phase", "doa", "lcmv", "gnss", "health", "errors"]
    }
    return BackendRuntime(config or StreamConfig(), loggers)


def _set_input_power_db(runtime: BackendRuntime, power_db: float) -> None:
    power_linear = 10.0 ** (float(power_db) / 10.0)
    with runtime._results_lock:
        runtime._latest_powers = np.full((4,), power_linear, dtype=np.float64)


def _jammer_enabled_config(**overrides: object) -> StreamConfig:
    values = {"jammer_detection_enabled": True, **overrides}
    return StreamConfig(**values)


class _SnapshotBridge:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> dict[str, object]:
        return dict(self._snapshot)


def test_jammer_detector_detects_raw_iq_power_rise_even_with_flat_spectrum() -> None:
    runtime = _runtime(
        _jammer_enabled_config(
            jammer_detection_power_rise_db=6.0,
            jammer_detection_consecutive_alarms=1,
        )
    )
    runtime._jammer_power_baseline_db = -40.0
    _set_input_power_db(runtime, -31.0)
    spectrum = np.ones((721,), dtype=np.float64)

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=100.0)

    assert status["assessed"] is True
    assert status["detected"] is True
    assert status["state"] == "detected"
    assert float(status["power_rise_db"]) == pytest.approx(9.0)
    assert float(status["spatial_peak_db"]) == pytest.approx(0.0)
    assert "Raw IQ power rise" in str(status["reason"])


def test_jammer_detector_logic_lives_in_runtime_jammer_detection_module() -> None:
    detector = JammerDetector(JammerDetectorConfig(power_rise_db=6.0, consecutive_alarms=1))
    detector.power_baseline_db = -40.0

    status = detector.assess(
        doa_deg=100.0,
        input_power_db=-31.0,
    )

    assert status["detected"] is True
    assert status["state"] == "detected"
    assert float(status["power_rise_db"]) == pytest.approx(9.0)


def test_jammer_detector_does_not_use_gnss_satellite_context_on_power_rise() -> None:
    runtime = _runtime(
        _jammer_enabled_config(
            jammer_detection_power_rise_db=6.0,
            jammer_detection_consecutive_alarms=1,
        )
    )
    runtime._jammer_power_baseline_db = -40.0
    runtime._gnss_bridge = _SnapshotBridge(
        {
            "tracking_count": 0,
            "stable_tracking_prns": [],
            "used_in_fix_count": 0,
            "pvt_current": False,
        }
    )
    _set_input_power_db(runtime, -31.0)

    status = runtime._assess_jammer_candidate(np.ones((721,), dtype=np.float64), doa_deg=100.0)

    assert status["detected"] is True
    assert "gnss_no_satellites" not in status
    assert "gnss_tracking_count" not in status
    assert "GNSS" not in str(status["reason"])


def test_jammer_detector_detects_power_rise_even_when_gnss_is_healthy() -> None:
    runtime = _runtime(
        _jammer_enabled_config(
            jammer_detection_power_rise_db=6.0,
            jammer_detection_consecutive_alarms=1,
        )
    )
    runtime._jammer_power_baseline_db = -67.7
    runtime._gnss_bridge = _SnapshotBridge(
        {
            "tracking_count": 10,
            "stable_tracking_prns": [5, 12, 13, 15, 18, 20, 23, 25, 26, 29],
            "used_in_fix_count": 9,
            "pvt_current": True,
        }
    )
    _set_input_power_db(runtime, -45.6)

    status = runtime._assess_jammer_candidate(np.ones((721,), dtype=np.float64), doa_deg=92.7)

    assert status["raw_power_alarm"] is True
    assert status["detected"] is True
    assert status["state"] == "detected"
    assert "gnss_impacted" not in status
    assert "gnss_tracking_count" not in status
    assert "GNSS remains healthy" not in str(status["reason"])


def test_jammer_detector_detects_raw_iq_power_rise_without_doa_evidence() -> None:
    runtime = _runtime(
        _jammer_enabled_config(
            jammer_detection_power_rise_db=6.0,
            jammer_detection_consecutive_alarms=1,
        )
    )
    runtime._jammer_power_baseline_db = -40.0
    _set_input_power_db(runtime, -31.0)
    spectrum = np.array([], dtype=np.float64)

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=float("nan"))

    assert status["assessed"] is True
    assert status["detected"] is True
    assert status["state"] == "detected"
    assert float(status["power_rise_db"]) == pytest.approx(9.0)
    assert "prominence_db" not in status


def test_jammer_detector_detects_spatial_peak_without_power_rise() -> None:
    runtime = _runtime(_jammer_enabled_config(jammer_detection_consecutive_alarms=1))
    runtime._jammer_power_baseline_db = -31.0
    _set_input_power_db(runtime, -31.0)
    spectrum = np.full((721,), 0.05, dtype=np.float64)
    spectrum[200] = 1.0

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=100.0)

    assert status["assessed"] is True
    assert status["detected"] is True
    assert status["state"] == "detected"
    assert status["spatial_alarm"] is True
    assert float(status["spatial_peak_db"]) == pytest.approx(13.0103, rel=1e-3)
    assert float(status["power_rise_db"]) < 1.0


def test_jammer_detector_reports_not_detected_for_flat_spectrum() -> None:
    runtime = _runtime(_jammer_enabled_config())
    runtime._jammer_power_baseline_db = -31.0
    _set_input_power_db(runtime, -31.0)
    spectrum = np.ones((721,), dtype=np.float64)

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=100.0)

    assert status["assessed"] is True
    assert status["detected"] is False
    assert status["state"] == "not_detected"
    assert float(status["spatial_peak_db"]) == pytest.approx(0.0)


def test_jammer_detector_initializes_power_baseline_before_detecting() -> None:
    runtime = _runtime(_jammer_enabled_config(jammer_detection_power_rise_db=6.0))
    _set_input_power_db(runtime, -31.0)
    spectrum = np.ones((721,), dtype=np.float64)

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=100.0)

    assert status["assessed"] is True
    assert status["detected"] is False
    assert status["state"] == "monitoring"
    assert status["power_baseline_db"] == pytest.approx(-31.0)
    assert status["reason"] == "Raw IQ power baseline initializing"


def test_jammer_detector_requires_consecutive_power_alarms_for_product_profile() -> None:
    detector = JammerDetector(JammerDetectorConfig(power_rise_db=6.0, consecutive_alarms=2))
    detector.power_baseline_db = -63.2

    first = detector.assess(doa_deg=188.0, input_power_db=-57.0)
    settled = detector.assess(doa_deg=188.0, input_power_db=-57.4)

    assert first["raw_power_alarm"] is True
    assert first["detected"] is False
    assert first["state"] == "suspected"
    assert first["raw_power_alarm_count"] == 1
    assert first["required_consecutive_alarms"] == 2
    assert settled["detected"] is False
    assert settled["raw_power_alarm_count"] == 0


def test_jammer_detector_can_be_disabled() -> None:
    runtime = _runtime(StreamConfig(jammer_detection_enabled=False))
    spectrum = np.full((721,), 0.05, dtype=np.float64)
    spectrum[200] = 1.0

    status = runtime._assess_jammer_candidate(spectrum, doa_deg=100.0)

    assert status["assessed"] is False
    assert status["detected"] is False
    assert status["state"] == "disabled"


def test_jammer_null_doa_tracks_detected_candidate_immediately() -> None:
    runtime = _runtime(StreamConfig())

    first_status = {"detected": True, "state": "detected", "reason": "test"}
    active, doa = runtime._jammer_mitigation_state(first_status, 183.49)

    assert active is True
    assert doa == pytest.approx(183.49)

    moved_status = {"detected": True, "state": "detected", "reason": "test"}
    active, doa = runtime._jammer_mitigation_state(moved_status, 358.0)

    assert active is True
    assert doa == pytest.approx(358.0)
    assert moved_status["mitigation_doa_deg"] == pytest.approx(358.0)
