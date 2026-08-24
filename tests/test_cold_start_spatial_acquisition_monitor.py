from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time

import numpy as np

import antijamming.gnss.cold_start_acquisition_monitor as monitor_module
from antijamming.gnss.cold_start_acquisition import (
    GPS_L1_HZ,
    SpatialPcpsResult,
)
from antijamming.gnss.cold_start_acquisition_monitor import (
    ColdStartSpatialAcquisitionMonitor,
    orthogonal_nullspace_rows,
)


def _context() -> dict[str, object]:
    return {
        "active": True,
        "mode": "rescue",
        "notch_frequency_offset_hz": 100_000.0,
        "jammer_vectors": np.asarray(
            [[1.0 + 0.0j], [0.0j], [0.0j], [0.0j]],
            dtype=np.complex128,
        ),
    }


def _monitor(
    tmp_path: Path,
    *,
    context_snapshot=_context,
) -> ColdStartSpatialAcquisitionMonitor:
    return ColdStartSpatialAcquisitionMonitor(
        sample_rate_hz=4_000_000.0,
        channel_count=4,
        phase_correction_vector=None,
        context_snapshot=context_snapshot,
        session_dir=tmp_path,
        session_id="test-session",
        logger=logging.getLogger("test.cold_start_monitor"),
        measurement_interval_s=0.25,
        dwell_count=5,
        min_observations=4,
    )


def test_orthogonal_nullspace_rows_are_nonredundant_and_equal_gain() -> None:
    null_vectors = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0j, 0.0],
            [0.0, -1.0j],
        ],
        dtype=np.complex128,
    )

    rows = orthogonal_nullspace_rows(null_vectors)

    assert rows.shape == (2, 4)
    np.testing.assert_allclose(rows.conj() @ null_vectors, 0.0, atol=1e-12)
    np.testing.assert_allclose(
        rows @ rows.conj().T,
        4.0 * np.eye(2),
        atol=1e-12,
    )


def test_monitor_publishes_only_after_four_physically_consistent_observations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rate = 4_000_000.0
    doppler_hz = -4_250.0
    vector = np.asarray([0.0, 1.0, 1.0j, 0.5], dtype=np.complex128)
    call_index = 0
    starts = (0, 1_966_080, 3_932_160, 5_898_240)
    fir_discard = 257

    def fake_pcps(*args, **kwargs) -> list[SpatialPcpsResult]:
        nonlocal call_index
        effective_start = starts[call_index] + fir_discard
        expected_slope = -doppler_hz / GPS_L1_HZ * rate
        elapsed = effective_start / rate
        absolute_phase = int(round(731.0 + expected_slope * elapsed)) % 4_000
        local_phase = (absolute_phase - effective_start) % 4_000
        call_index += 1
        return [
            SpatialPcpsResult(
                prn=prn,
                doppler_hz=doppler_hz if prn == 14 else 0.0,
                code_phase_samples=local_phase if prn == 14 else 0,
                peak_to_median_db=9.0 if prn == 14 else 0.0,
                peak_to_mean_db=8.8 if prn == 14 else 0.0,
                projected_spatial_vector=vector,
                spatial_projector_dominant_fraction=(
                    0.98 if prn == 14 else 0.2
                ),
            )
            for prn in range(1, 33)
        ]

    monkeypatch.setattr(monitor_module, "noncoherent_spatial_pcps", fake_pcps)
    monitor = _monitor(tmp_path)
    monitor.start()
    raw = np.zeros((4, 1_000), dtype=np.complex64)
    try:
        for index, start in enumerate(starts):
            monitor._evaluate(start, raw, float(index))
            vectors = monitor.desired_vectors_snapshot()
            if index < 3:
                assert vectors == {}
        vectors = monitor.desired_vectors_snapshot()
        assert set(vectors) == {"G14"}
        assert vectors["G14"]["vector_source"] == "cold_start_spatial_pcps"
        assert vectors["G14"]["quality_pass"] is True
        np.testing.assert_allclose(
            vectors["G14"]["desired_spatial_vector"], vector
        )
    finally:
        monitor.stop()

    records = [
        json.loads(line)
        for line in (tmp_path / "cold_start_spatial_acquisition.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    stop = next(
        record
        for record in records
        if record["event"] == "cold_start_spatial_acquisition_stop"
    )
    assert stop["thread_stopped"] is True


def test_monitor_stop_cancels_an_in_progress_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entered = threading.Event()

    def cancellable_pcps(*args, **kwargs):
        entered.set()
        cancel_requested = kwargs["cancel_requested"]
        while not cancel_requested():
            time.sleep(0.005)
        raise InterruptedError("spatial PCPS cancelled")

    monkeypatch.setattr(
        monitor_module,
        "noncoherent_spatial_pcps",
        cancellable_pcps,
    )
    monitor = _monitor(tmp_path)
    monitor.start()
    monitor.submit(0, np.zeros((4, 1_000), dtype=np.complex64))
    assert entered.wait(timeout=1.0)

    started = time.monotonic()
    monitor.stop()

    assert time.monotonic() - started < 1.0
    records = [
        json.loads(line)
        for line in (tmp_path / "cold_start_spatial_acquisition.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    stop = records[-1]
    assert stop["event"] == "cold_start_spatial_acquisition_stop"
    assert stop["thread_stopped"] is True
    assert stop["evaluation_count"] == 0


def test_classifier_requires_four_observations_and_vetoes_on_real_prn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rate = 4_000_000.0
    doppler_hz = 2_500.0
    vector = np.asarray([1.0, 0.2j, -0.4, 0.7j], dtype=np.complex128)
    starts = (0, 1_966_080, 3_932_160, 5_898_240)
    call_index = 0

    def classify_context() -> dict[str, object]:
        return {
            "active": True,
            "mode": "classify",
            "notch_frequency_offset_hz": None,
            "jammer_vectors": np.zeros((4, 0), dtype=np.complex128),
        }

    def fake_pcps(*args, **kwargs) -> list[SpatialPcpsResult]:
        nonlocal call_index
        sample_start = starts[call_index]
        expected_slope = -doppler_hz / GPS_L1_HZ * rate
        elapsed = sample_start / rate
        absolute_phase = int(round(1_103.0 + expected_slope * elapsed)) % 4_000
        local_phase = (absolute_phase - sample_start) % 4_000
        call_index += 1
        return [
            SpatialPcpsResult(
                prn=prn,
                doppler_hz=doppler_hz if prn == 9 else 0.0,
                code_phase_samples=local_phase if prn == 9 else 0,
                peak_to_median_db=9.0 if prn == 9 else 0.0,
                peak_to_mean_db=8.8 if prn == 9 else 0.0,
                projected_spatial_vector=vector,
                spatial_projector_dominant_fraction=0.98 if prn == 9 else 0.2,
            )
            for prn in range(1, 33)
        ]

    monkeypatch.setattr(monitor_module, "noncoherent_spatial_pcps", fake_pcps)
    monitor = _monitor(tmp_path, context_snapshot=classify_context)
    monitor.start()
    try:
        raw = np.zeros((4, 1_000), dtype=np.complex64)
        for index, start in enumerate(starts):
            monitor._evaluate(start, raw, float(index))
            snapshot = monitor.classification_snapshot()
            if index < 3:
                assert snapshot["sufficient_observations"] is False
        snapshot = monitor.classification_snapshot()
        assert snapshot["sufficient_observations"] is True
        assert snapshot["gnss_consistency_veto"] is True
        assert snapshot["accepted_prns"] == [9]
    finally:
        monitor.stop()


def test_classifier_can_report_sufficient_observations_without_gnss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def classify_context() -> dict[str, object]:
        return {
            "active": True,
            "mode": "classify",
            "notch_frequency_offset_hz": None,
            "jammer_vectors": np.zeros((4, 0), dtype=np.complex128),
        }

    def false_pcps(*args, **kwargs) -> list[SpatialPcpsResult]:
        return [
            SpatialPcpsResult(
                prn=prn,
                doppler_hz=0.0,
                code_phase_samples=0,
                peak_to_median_db=2.0,
                peak_to_mean_db=1.0,
                projected_spatial_vector=np.ones((4,), dtype=np.complex128),
                spatial_projector_dominant_fraction=0.2,
            )
            for prn in range(1, 33)
        ]

    monkeypatch.setattr(monitor_module, "noncoherent_spatial_pcps", false_pcps)
    monitor = _monitor(tmp_path, context_snapshot=classify_context)
    monitor.start()
    try:
        raw = np.zeros((4, 1_000), dtype=np.complex64)
        for index, start in enumerate((0, 1_966_080, 3_932_160, 5_898_240)):
            monitor._evaluate(start, raw, float(index))
        snapshot = monitor.classification_snapshot()
        assert snapshot["sufficient_observations"] is True
        assert snapshot["gnss_consistency_veto"] is False
        assert snapshot["accepted_prns"] == []
    finally:
        monitor.stop()
