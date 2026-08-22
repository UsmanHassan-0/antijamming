from __future__ import annotations

import logging
from pathlib import Path
import select

import numpy as np
import pytest

from antijamming.config import StreamConfig
from antijamming.gnss import (
    GnssSdrBridge,
    PerPrnMeasuredVectorBeamformerBank,
    SharedU1DesiredVectorMonitor,
    SharedU1PhaseCompensationBank,
    apply_shared_phase_fanout,
)
from antijamming.gnss.shared_u1_phase_compensation import (
    gps_l1_ca_code,
    phase_invariant_coherence,
)
from antijamming.gnss.sdr_bridge.constants import (
    gnss_input_filter_group_delay_samples,
    gnss_input_filter_tap_count,
)
from antijamming.gnss.sdr_bridge.fifo import PER_SOURCE_FIFO_STRIPE_SAMPLES
from antijamming.logging import setup_logging
from antijamming.runtime.backend import BackendRuntime


def _loggers() -> dict[str, logging.Logger]:
    return {
        name: logging.getLogger(f"test.shared_u1_phase.{name}")
        for name in ("app", "gnss", "errors")
    }


def _monitor_tracking_entry(
    prn: int,
    *,
    channel: int = 0,
    counter: int = 500,
) -> dict[str, object]:
    return {
        "system": "GPS",
        "signal": "1C",
        "prn": prn,
        "satellite_id": f"G{prn:02d}",
        "channel": channel,
        "tracking_sample_counter": counter,
        "cn0_db_hz": 45.0,
        "carrier_doppler_hz": 0.0,
    }


def _monitor_snapshot(
    prn: int,
    *,
    channel: int = 0,
    counter: int = 500,
    state: str = "tracking",
) -> dict[str, object]:
    tracking = _monitor_tracking_entry(
        prn, channel=channel, counter=counter
    )
    return {
        "tracking_monitor": [tracking],
        "prns": [
            {
                "prn": prn,
                "satellite_id": f"G{prn:02d}",
                "channel": channel,
                "state": state,
                "tracking_monitor_prn": prn,
            }
        ],
    }


def _add_monitor_quality_measurement(
    monitor: SharedU1DesiredVectorMonitor,
    *,
    prn: int,
    spatial: np.ndarray,
    counter: int,
    source_index: int = 0,
) -> dict[str, object]:
    code = gps_l1_ca_code(prn).astype(np.complex64)
    raw = np.zeros((4, 1031), dtype=np.complex64)
    raw[:, 5:1028] = spatial[:, None] * code[None, :]
    monitor._append_span(
        counter - 1028,
        raw,
        np.ones((1, 4), dtype=np.complex128),
    )
    measurement = monitor._correlate(
        {}, f"G{prn:02d}", prn, counter, 0.0, 45.0, source_index
    )
    assert measurement is not None
    assert measurement["quality_pass"] is True
    return measurement


def test_shared_u1_phase_rows_preserve_full_complex_response() -> None:
    common = np.ones((4,), dtype=np.complex128)
    # One shared spatial solution with a null toward [1,1,1,1].
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired_g03 = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    desired_g04 = np.asarray([0.2j, 1.0, -0.4j, 0.7], dtype=np.complex128)
    now = 100.0
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_g03,
            "updated_monotonic": now,
            "tracking_sample_counter": 1000,
        },
        "G04": {
            "desired_spatial_vector": desired_g04,
            "updated_monotonic": now,
            "tracking_sample_counter": 1000,
        },
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(3, 4),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )

    before, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=now,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=now + 0.01,
    )

    jammer = np.ones((4,), dtype=np.complex128)
    for row_index, (satellite, desired) in enumerate(
        (("G03", desired_g03), ("G04", desired_g04))
    ):
        row = applied[row_index]
        # Only one scalar may differ from the shared solution.
        ratios = row / shared
        assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
        old_response = np.vdot(before[row_index], desired)
        applied_response = np.vdot(row, desired)
        assert applied_response == pytest.approx(old_response, abs=1e-12)
        exact_scale = np.conj(old_response / np.vdot(shared, desired))
        assert np.linalg.norm(row) == pytest.approx(
            abs(exact_scale) * np.linalg.norm(shared)
        )
        # A scalar cannot move or fill the shared spatial null.
        assert abs(np.vdot(row, jammer)) < 1e-12
        assert status[satellite]["phase_compensation_applied"] is True
        assert status[satellite]["independent_per_prn_lcmv"] is False
        assert status[satellite]["amplitude_compensation_db"] == pytest.approx(
            20.0 * np.log10(abs(exact_scale))
        )


def test_shared_u1_phase_renderer_pins_each_prn_without_global_fifo_backpressure(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(3, 4, 7, 8),
        gnss_sdr_runtime_dir=tmp_path / "runtime",
        gnss_sdr_log_dir=tmp_path / "glog",
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    rendered = bridge._render_config()

    assert "GNSS-SDR.num_sources=4" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=false" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=true" not in rendered
    for index, prn in enumerate((3, 4, 7, 8)):
        assert f"SignalSource{index}.filename=" in rendered
        assert f"gnss_iq_G{prn:02d}.fifo" in rendered
        assert f"Channel{index}.satellite={prn}" in rendered
        assert f"Channel{index}.RF_channel_ID={index}" in rendered


def test_ten_source_fifo_fanout_writes_one_full_chunk_per_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    satellites = tuple(range(1, 11))
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=satellites,
        samples_per_chunk=32_768,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._fifo_fds = list(range(len(satellites)))
    calls: list[tuple[int, int]] = []

    class WritablePoll:
        def __init__(self) -> None:
            self._fds: list[int] = []

        def register(self, fd: int, _event_mask: int) -> None:
            self._fds.append(fd)

        def unregister(self, fd: int) -> None:
            self._fds.remove(fd)

        def poll(self, _timeout_ms: int) -> list[tuple[int, int]]:
            return [(fd, select.POLLOUT) for fd in self._fds]

    def fake_write(fd: int, payload: memoryview) -> int:
        calls.append((fd, len(payload)))
        return len(payload)

    monkeypatch.setattr(
        "antijamming.gnss.sdr_bridge.bridge.os.write",
        fake_write,
    )
    monkeypatch.setattr(
        "antijamming.gnss.sdr_bridge.bridge.select.poll",
        WritablePoll,
    )
    samples = np.zeros((len(satellites), 32_768), dtype=np.complex64)

    assert bridge.write(samples) is True
    chunk_bytes = samples.shape[1] * np.dtype(np.complex64).itemsize
    assert PER_SOURCE_FIFO_STRIPE_SAMPLES == samples.shape[1]
    assert calls == [(index, chunk_bytes) for index in range(len(satellites))]
    assert bridge._fifo_max_source_lead_bytes == chunk_bytes
    assert bridge._write_bytes == len(satellites) * chunk_bytes


def test_shared_u1_phase_renderer_uses_independent_dynamic_channel_slots(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(),
        gnss_1c_channel_count=4,
        gnss_channels_in_acquisition=4,
        gnss_sdr_runtime_dir=tmp_path / "runtime",
        gnss_sdr_log_dir=tmp_path / "glog",
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    rendered = bridge._render_config()

    assert "GNSS-SDR.num_sources=4" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=false" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=true" not in rendered
    assert ".satellite=" not in rendered
    for index in range(4):
        assert f"SignalSource{index}.filename=" in rendered
        assert f"gnss_iq_channel_{index:02d}.fifo" in rendered
        assert f"SignalConditioner{index}.implementation=Signal_Conditioner" in rendered
        assert f"Channel{index}.signal=1C" in rendered
        assert f"Channel{index}.RF_channel_ID={index}" in rendered


def test_dynamic_source_reassignment_discards_old_prn_phase_state() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired_g03 = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    desired_g10 = np.asarray([0.1j, 1.0, -0.4j, 0.7], dtype=np.complex128)
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_g03,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        },
        "G10": {
            "desired_spatial_vector": desired_g10,
            "updated_monotonic": 10.02,
            "tracking_sample_counter": 2000,
        },
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(),
        source_count=2,
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )

    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(3, 4),
        enabled_now=False,
        now_monotonic=10.0,
    )
    old_rows, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(3, 4),
        enabled_now=True,
        now_monotonic=10.01,
    )
    reassigned_rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(10, 4),
        enabled_now=True,
        now_monotonic=10.02,
    )

    assert not np.allclose(old_rows[0], common)
    assert np.allclose(reassigned_rows[0], shared)
    assert status["G10"]["source_index"] == 0
    assert status["G10"]["desired_vector_available"] is False
    assert status["G10"]["source"] == "shared_measured_u1_acquisition_new_prn"
    assert "G03" not in status


def test_unmapped_and_new_prn_sources_use_shared_protection_during_jamming() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    bank = SharedU1PhaseCompensationBank(
        satellites=(),
        source_count=2,
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )

    waiting, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={},
        source_satellites=(10, None),
        enabled_now=True,
        now_monotonic=10.0,
    )
    protected, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={},
        source_satellites=(10, None),
        enabled_now=True,
        now_monotonic=10.01,
    )

    assert np.allclose(waiting, np.vstack((common, common)))
    assert np.allclose(protected, np.vstack((shared, shared)))
    assert status["G10"]["source"] == "shared_measured_u1_acquisition_new_prn"
    assert status["source_01"]["source"] == (
        "shared_measured_u1_acquisition_waiting_for_channel_prn"
    )
    assert status["source_01"]["shared_protection_bridge_applied"] is True


def test_large_exact_gain_request_preserves_complex_response_when_norm_is_safe() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([0.45, 0.0, 0.0, 0.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.complex128)
    vectors = {
        "G04": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(4,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
        max_weight_norm=8.0,
    )

    before, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )

    assert status["G04"]["requested_exact_amplitude_compensation_db"] > 6.0
    assert status["G04"]["amplitude_compensation_db"] == pytest.approx(
        status["G04"]["requested_exact_amplitude_compensation_db"]
    )
    assert status["G04"]["phase_compensation_applied"] is True
    assert status["G04"]["phase_compensation_guard_reason"] is None
    assert np.linalg.norm(applied[0]) == pytest.approx(1.0)
    assert np.vdot(applied[0], desired) == pytest.approx(
        np.vdot(before[0], desired), abs=1e-12
    )


def test_phase_compensation_still_rejects_unsafe_weight_norm() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, 10.0, 10.0, 10.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.complex128)
    vectors = {
        "G04": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(4,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
        max_weight_norm=8.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )

    assert np.allclose(applied[0], common)
    assert status["G04"]["phase_compensation_applied"] is False
    assert status["G04"]["phase_compensation_guard_reason"] == (
        "phase_compensated_weight_norm_exceeded"
    )


def test_shared_u1_phase_runtime_labels_transport_and_actual_fifo_state(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        log_dir=tmp_path,
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(3, 4, 7, 8),
    )
    runtime = BackendRuntime(cfg, setup_logging(tmp_path))

    assert runtime._gnss_handoff_mode_label() == (
        "independent_per_prn_measured_vector_fanout"
    )
    assert runtime._fifo_output_source_label() == (
        "independent_per_prn_measured_vector_fanout"
    )


def test_shared_u1_phase_waits_for_covariance_worker_without_missing_onset() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    vectors = {
        "G03": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    waiting, waiting_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )
    applied, applied_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.02,
    )

    assert np.allclose(waiting[0], common)
    assert waiting_status["G03"]["shared_measured_u1_available"] is False
    assert applied_status["G03"]["phase_compensation_applied"] is True
    assert np.angle(
        np.vdot(applied[0], desired) * np.conj(np.vdot(waiting[0], desired))
    ) == pytest.approx(0.0, abs=1e-12)
    assert np.linalg.norm(applied[0]) == pytest.approx(np.linalg.norm(shared))


def test_prn_monitor_recovers_calibrated_spatial_iq_from_one_code_period(
    tmp_path: Path,
) -> None:
    """Code despreading must retain channel amplitude/phase, not carrier phase."""

    fs = 1_023_000.0
    prn = 3
    code = gps_l1_ca_code(prn).astype(np.complex64)
    spatial = np.asarray([1.0, 0.6 + 0.4j, -0.3 + 0.8j, 0.7 - 0.2j])
    correction = np.asarray([1.0, 1.0j, 0.5 - 0.2j, -0.8 + 0.1j])
    common_carrier_phase = np.exp(0.73j)
    raw = np.zeros((4, 1031), dtype=np.complex64)
    raw[:, 5:1028] = (
        spatial[:, None] * common_carrier_phase * code[None, :]
    )
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=fs,
        channel_count=4,
        phase_correction_vector=tuple(correction),
        tracking_snapshot=lambda: {},
        session_dir=tmp_path,
        session_id="synthetic",
        logger=logging.getLogger("test.shared_u1_phase.monitor"),
        satellites=(prn,),
        min_quality_measurements=1,
    )
    monitor._append_span(
        -5,
        raw,
        np.ones((1, 4), dtype=np.complex128),
    )

    measurement = monitor._correlate(
        {"carrier_phase_rads": 123_456.75},
        "G03",
        prn,
        1023,
        0.0,
        45.0,
        0,
    )
    recovered = monitor.desired_vectors_snapshot()["G03"]

    assert measurement is not None
    assert measurement["quality_pass"] is True
    assert measurement["prompt_vs_wrong_code_db"] > 40.0
    assert measurement["reported_carrier_phase_rads"] == 123_456.75
    assert recovered["quality_pass_count"] == 1
    assert phase_invariant_coherence(
        recovered["desired_spatial_vector"],
        spatial * correction,
    ) > 1.0 - 1e-12


def test_prn_monitor_compensates_post_fir_tracking_counter_delay(
    tmp_path: Path,
) -> None:
    """Tracking counters after the FIR must address the matching pre-FIR IQ."""

    fs = 4_000_000.0
    delay = gnss_input_filter_group_delay_samples(fs)
    assert gnss_input_filter_tap_count(fs) == 55
    assert delay == 27

    prn = 3
    code = gps_l1_ca_code(prn)
    code_rate_hz = 1_023_000.0
    length = 4_000
    chips = np.floor(
        np.arange(length, dtype=np.float64) * code_rate_hz / fs
    ).astype(np.int64) % code.size
    replica = code[chips].astype(np.complex64)
    spatial = np.asarray([1.0, 0.6 + 0.4j, -0.3 + 0.8j, 0.7 - 0.2j])
    rng = np.random.default_rng(220826)
    raw = (
        2.0
        / np.sqrt(2.0)
        * (
            rng.standard_normal((4, length + 70))
            + 1j * rng.standard_normal((4, length + 70))
        )
    ).astype(np.complex64)
    raw[:, 5 : 5 + length] += spatial[:, None] * replica[None, :]
    raw_span_start = 100_000 - 5
    raw_code_end_counter = 100_000 + length
    tracking_counter = raw_code_end_counter + delay

    aligned = SharedU1DesiredVectorMonitor(
        sample_rate_hz=fs,
        frontend_group_delay_samples=delay,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: {},
        session_dir=tmp_path / "aligned",
        session_id="aligned",
        logger=logging.getLogger("test.shared_u1_phase.fir_aligned"),
        satellites=(prn,),
        min_quality_measurements=1,
    )
    aligned._append_span(
        raw_span_start,
        raw,
        np.ones((1, 4), dtype=np.complex128),
    )
    aligned_measurement = aligned._correlate(
        {}, "G03", prn, tracking_counter, 0.0, 45.0, 0
    )

    uncorrected = SharedU1DesiredVectorMonitor(
        sample_rate_hz=fs,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: {},
        session_dir=tmp_path / "uncorrected",
        session_id="uncorrected",
        logger=logging.getLogger("test.shared_u1_phase.fir_uncorrected"),
        satellites=(prn,),
        min_quality_measurements=1,
    )
    uncorrected._append_span(
        raw_span_start,
        raw,
        np.ones((1, 4), dtype=np.complex128),
    )
    uncorrected_measurement = uncorrected._correlate(
        {}, "G03", prn, tracking_counter, 0.0, 45.0, 0
    )

    assert aligned_measurement is not None
    assert aligned_measurement["quality_pass"] is True
    assert aligned_measurement["tracking_sample_counter"] == tracking_counter
    assert aligned_measurement["raw_sample_counter"] == raw_code_end_counter
    recovered = aligned.desired_vectors_snapshot()["G03"]
    assert phase_invariant_coherence(
        recovered["desired_spatial_vector"], spatial
    ) > 0.998
    # The former scalar wrong-code gate can falsely pass even at the wrong
    # epoch.  Its recovered spatial vector is nevertheless noise dominated.
    assert uncorrected_measurement is not None
    assert uncorrected_measurement["quality_pass"] is True
    uncorrected_vector = uncorrected.desired_vectors_snapshot()["G03"]
    assert phase_invariant_coherence(
        uncorrected_vector["desired_spatial_vector"], spatial
    ) < 0.85


def test_shared_u1_phase_rephases_every_later_covariance_update() -> None:
    common = np.ones((4,), dtype=np.complex128)
    first = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    second = np.asarray([0.7 - 0.1j, 0.1 - 0.4j, 1.2 + 0.1j, 0.6 + 0.3j])
    desired = np.asarray([1.0, 0.8 + 0.1j, 0.5 - 0.2j, 0.4 + 0.3j])
    desired /= np.linalg.norm(desired)
    vectors = {
        "G05": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 20.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(5,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=32_768,
        transition_s=1.0,
    )
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=20.0,
    )
    first_rows, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=first,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=20.01,
    )
    second_rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=second,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=21.01,
    )

    assert not np.allclose(first_rows, second_rows)
    assert np.vdot(second_rows[0], desired) == pytest.approx(
        np.vdot(baseline[0], desired), abs=1e-12
    )
    ratios = second_rows[0] / second
    assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
    assert status["G05"]["amplitude_compensation_db"] == pytest.approx(
        20.0 * np.log10(abs(ratios[0]))
    )


def test_jammer_off_ramp_preserves_response_phase_at_every_chunk() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    desired = np.asarray([1.0, 0.8 + 0.1j, 0.5 - 0.2j, 0.4 + 0.3j])
    desired /= np.linalg.norm(desired)
    vectors = {
        "G05": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 20.0,
            "tracking_sample_counter": 1000,
        }
    }
    # Ten samples per chunk and a one-second transition produce ten exact
    # intermediate points, making every ramp step observable in this test.
    bank = SharedU1PhaseCompensationBank(
        satellites=(5,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=1.0,
    )
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=20.0,
    )
    nulled, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=20.01,
    )
    preserved_response = np.vdot(nulled[0], desired)
    assert not np.allclose(nulled, baseline)

    ramp_rows: list[np.ndarray] = []
    for chunk_index in range(10):
        rows, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=shared,
            shared_measured_u1_available=True,
            desired_vectors={},
            enabled_now=False,
            now_monotonic=21.0 + chunk_index / 10.0,
        )
        ramp_rows.append(np.array(rows[0], copy=True))
        assert np.angle(
            np.vdot(rows[0], desired) * np.conj(preserved_response)
        ) == pytest.approx(0.0, abs=1e-12)
        assert status["G05"]["independent_per_prn_lcmv"] is False

    assert not np.allclose(ramp_rows[0], ramp_rows[-1])
    final_response = np.vdot(ramp_rows[-1], desired)
    assert abs(final_response) == pytest.approx(abs(np.vdot(baseline[0], desired)))
    ratios = ramp_rows[-1] / common
    assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
    assert abs(ratios[0]) == pytest.approx(1.0)


def test_shared_u1_phase_can_skip_per_chunk_status_allocation() -> None:
    bank = SharedU1PhaseCompensationBank(
        satellites=(3, 4, 7, 8),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=32_768,
        transition_s=1.0,
    )
    rows, status = bank.advance(
        shared_common_weights=np.ones(4, dtype=np.complex128),
        shared_measured_u1_weights=np.ones(4, dtype=np.complex128),
        shared_measured_u1_available=False,
        desired_vectors={},
        enabled_now=False,
        now_monotonic=1.0,
        emit_status=False,
    )

    assert rows.shape == (4, 4)
    assert status == {}


def test_scalar_fanout_matches_general_four_channel_matrix() -> None:
    rng = np.random.default_rng(42)
    raw = (
        rng.standard_normal((4, 4096))
        + 1j * rng.standard_normal((4, 4096))
    ).astype(np.complex64)
    shared = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    scales = np.exp(1j * np.linspace(-0.8, 0.9, 10)) * np.linspace(0.7, 1.3, 10)
    rows = scales[:, None] * shared[None, :]
    correction = np.asarray([1.0, -0.9 + 0.2j, 0.7 - 0.4j, 0.3 - 1.0j])

    actual, fast_path = apply_shared_phase_fanout(raw, rows, correction)
    effective = np.asarray(
        np.conj(rows) * correction.astype(np.complex64)[None, :],
        dtype=np.complex64,
    )
    expected = np.ascontiguousarray(effective @ raw, dtype=np.complex64)

    assert fast_path is True
    np.testing.assert_allclose(actual, expected, rtol=5e-6, atol=2e-6)


def test_noncollinear_transition_rows_use_general_matrix() -> None:
    rng = np.random.default_rng(43)
    raw = (
        rng.standard_normal((4, 256))
        + 1j * rng.standard_normal((4, 256))
    ).astype(np.complex64)
    rows = np.asarray(
        [
            [1.0, 1.0, 1.0, 1.0],
            [0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j],
        ],
        dtype=np.complex128,
    )
    correction = np.ones(4, dtype=np.complex64)

    actual, fast_path = apply_shared_phase_fanout(raw, rows, correction)
    expected = np.asarray(np.conj(rows).astype(np.complex64) @ raw, dtype=np.complex64)

    assert fast_path is False
    np.testing.assert_array_equal(actual, expected)


def test_per_prn_bank_uses_distinct_measured_rows_without_jammer() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired_3 = np.asarray([1.0, 0.8j, -0.4, 0.2 - 0.1j])
    desired_4 = np.asarray([0.3j, 1.0, 0.6 - 0.2j, -0.7])
    desired_3 /= np.linalg.norm(desired_3)
    desired_4 /= np.linalg.norm(desired_4)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3, 4),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_3,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        },
        "G04": {
            "desired_spatial_vector": desired_4,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 200,
        },
    }

    rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )

    assert not np.allclose(rows[0], rows[1])
    assert np.vdot(rows[0], desired_3) == pytest.approx(
        np.vdot(common, desired_3), abs=1e-12
    )
    assert np.vdot(rows[1], desired_4) == pytest.approx(
        np.vdot(common, desired_4), abs=1e-12
    )
    assert status["G03"]["independent_per_prn_beamforming"] is True
    assert status["G04"]["independent_per_prn_beamforming"] is True
    assert status["G03"]["desired_vector_frozen"] is False


def test_per_prn_bank_lcmv_nulls_jammer_and_preserves_each_prn() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired_3 = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    desired_4 = np.asarray([0.2j, 1.0, 0.5 - 0.3j, -0.6])
    jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    desired_3 /= np.linalg.norm(desired_3)
    desired_4 /= np.linalg.norm(desired_4)
    jammer /= np.linalg.norm(jammer)
    covariance = (
        25.0 * np.outer(jammer, np.conj(jammer))
        + np.eye(4, dtype=np.complex128)
    )
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3, 4),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_3,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        },
        "G04": {
            "desired_spatial_vector": desired_4,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 200,
        },
    }
    before, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    protected, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=np.asarray([1.0, -1.0, 1.0, -1.0]),
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=10.1,
    )

    assert not np.allclose(protected[0], protected[1])
    for index, (satellite, desired) in enumerate(
        (("G03", desired_3), ("G04", desired_4))
    ):
        assert np.vdot(protected[index], desired) == pytest.approx(
            np.vdot(before[index], desired), abs=1e-10
        )
        assert abs(np.vdot(protected[index], jammer)) < 1e-10
        assert status[satellite]["independent_per_prn_lcmv"] is True
        assert status[satellite]["continuity_residual_abs"] < 1e-10


def test_per_prn_bank_adopts_a_new_prn_during_jamming() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    jammer /= np.linalg.norm(jammer)
    covariance = 20.0 * np.outer(jammer, np.conj(jammer)) + np.eye(4)
    desired = np.asarray([0.3j, 1.0, 0.5 - 0.3j, -0.6])
    desired /= np.linalg.norm(desired)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(),
        source_count=1,
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    waiting, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={},
        source_satellites=(4,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=10.0,
    )
    adopted, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={
            "G04": {
                "desired_spatial_vector": desired,
                "updated_monotonic": 11.0,
                "tracking_sample_counter": 500,
            }
        },
        source_satellites=(4,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=11.0,
    )

    np.testing.assert_allclose(waiting[0], shared)
    assert not np.allclose(adopted[0], shared)
    assert abs(np.vdot(adopted[0], jammer)) < 1e-10
    assert status["G04"]["independent_per_prn_lcmv"] is True


def test_per_prn_bank_same_prn_reacquisition_requires_a_new_epoch_vector() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    old_desired = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    new_desired = np.asarray([0.2j, 1.0, 0.5 - 0.3j, -0.6])
    jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    old_desired /= np.linalg.norm(old_desired)
    new_desired /= np.linalg.norm(new_desired)
    jammer /= np.linalg.norm(jammer)
    covariance = 20.0 * np.outer(jammer, np.conj(jammer)) + np.eye(4)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(),
        source_count=1,
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    old_payload = {
        "G03": {
            "desired_spatial_vector": old_desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        }
    }
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=old_payload,
        source_satellites=(3,),
        enabled_now=False,
        now_monotonic=10.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=old_payload,
        source_satellites=(3,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=10.1,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={},
        source_satellites=(None,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=11.0,
    )

    stale_rows, stale_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": new_desired,
                "updated_monotonic": 10.9,
                "tracking_sample_counter": 200,
            }
        },
        source_satellites=(3,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=11.1,
    )
    np.testing.assert_allclose(stale_rows[0], shared)
    assert stale_status["G03"]["desired_vector_available"] is False
    assert stale_status["G03"]["tracking_sample_counter"] is None

    fresh_rows, fresh_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": new_desired,
                "updated_monotonic": 11.2,
                "tracking_sample_counter": 201,
            }
        },
        source_satellites=(3,),
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=11.2,
    )
    assert fresh_status["G03"]["tracking_sample_counter"] == 201
    assert fresh_status["G03"]["independent_per_prn_lcmv"] is True
    assert abs(np.vdot(fresh_rows[0], jammer)) < 1e-10


def test_per_prn_bank_reassignment_rejects_cached_vector_from_old_epoch() -> None:
    common = np.ones((4,), dtype=np.complex128)
    first = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    second = np.asarray([0.2j, 1.0, 0.5 - 0.3j, -0.6])
    first /= np.linalg.norm(first)
    second /= np.linalg.norm(second)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(),
        source_count=1,
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": first,
                "updated_monotonic": 10.0,
                "tracking_sample_counter": 100,
            }
        },
        source_satellites=(3,),
        enabled_now=False,
        now_monotonic=10.0,
    )

    stale_rows, stale_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G04": {
                "desired_spatial_vector": second,
                "updated_monotonic": 10.9,
                "tracking_sample_counter": 200,
            }
        },
        source_satellites=(4,),
        enabled_now=False,
        now_monotonic=11.0,
    )
    np.testing.assert_allclose(stale_rows[0], common)
    assert stale_status["G04"]["desired_vector_available"] is False

    _, fresh_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G04": {
                "desired_spatial_vector": second,
                "updated_monotonic": 11.1,
                "tracking_sample_counter": 201,
            }
        },
        source_satellites=(4,),
        enabled_now=False,
        now_monotonic=11.1,
    )
    assert fresh_status["G04"]["tracking_sample_counter"] == 201
    assert fresh_status["G04"]["independent_per_prn_beamforming"] is True


def test_per_prn_bank_tracks_a_changed_desired_vector_without_response_jump() -> None:
    common = np.ones((4,), dtype=np.complex128)
    first = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    second = np.asarray([0.8 + 0.1j, 0.5j, -0.5, 0.6 - 0.1j])
    first /= np.linalg.norm(first)
    second /= np.linalg.norm(second)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    initial, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": first,
                "updated_monotonic": 10.0,
                "tracking_sample_counter": 100,
            }
        },
        enabled_now=False,
        now_monotonic=10.0,
    )
    response_immediately_before_update = np.vdot(initial[0], second)
    updated, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": second,
                "updated_monotonic": 11.0,
                "tracking_sample_counter": 200,
            }
        },
        enabled_now=False,
        now_monotonic=11.0,
    )

    assert np.vdot(updated[0], second) == pytest.approx(
        response_immediately_before_update, abs=1e-12
    )
    assert status["G03"]["desired_vector_frozen"] is False
    assert status["G03"]["tracking_sample_counter"] == 200


def test_per_prn_bank_freezes_pre_jammer_vector_until_release() -> None:
    common = np.ones((4,), dtype=np.complex128)
    before_vector = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    contaminated_vector = np.asarray([0.2j, 1.0, 0.5 - 0.3j, -0.6])
    jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    before_vector /= np.linalg.norm(before_vector)
    contaminated_vector /= np.linalg.norm(contaminated_vector)
    jammer /= np.linalg.norm(jammer)
    covariance = 20.0 * np.outer(jammer, np.conj(jammer)) + np.eye(4)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )

    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": before_vector,
                "updated_monotonic": 10.0,
                "tracking_sample_counter": 100,
            }
        },
        enabled_now=False,
        now_monotonic=10.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=True,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": before_vector,
                "updated_monotonic": 10.0,
                "tracking_sample_counter": 100,
            }
        },
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=10.1,
    )
    protected, active_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=True,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": contaminated_vector,
                "updated_monotonic": 11.0,
                "tracking_sample_counter": 200,
            }
        },
        enabled_now=True,
        covariance=covariance,
        jammer_vector=jammer,
        now_monotonic=11.0,
    )

    frozen = np.asarray(
        active_status["G03"]["desired_spatial_vector"]["real"]
    ) + 1j * np.asarray(
        active_status["G03"]["desired_spatial_vector"]["imag"]
    )
    assert active_status["G03"]["desired_vector_frozen"] is True
    assert active_status["G03"]["tracking_sample_counter"] == 100
    assert phase_invariant_coherence(frozen, before_vector) > 1.0 - 1e-12
    assert abs(np.vdot(protected[0], jammer)) < 1e-10

    _, released_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": contaminated_vector,
                "updated_monotonic": 12.0,
                "tracking_sample_counter": 200,
            }
        },
        enabled_now=False,
        now_monotonic=12.0,
    )
    assert released_status["G03"]["desired_vector_frozen"] is False
    assert released_status["G03"]["tracking_sample_counter"] == 200


def test_per_prn_jammer_transition_is_not_restarted_by_context_updates() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    desired /= np.linalg.norm(desired)
    jammer /= np.linalg.norm(jammer)
    covariance = 20.0 * np.outer(jammer, np.conj(jammer)) + np.eye(4)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=1.0,
    )
    vectors = {
        "G03": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        }
    }
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    preserved_response = np.vdot(baseline[0], desired)

    progresses = []
    rows = baseline
    for index in range(10):
        shared = np.asarray(
            [1.0, -1.0 + 0.001j * index, 1.0, -1.0],
            dtype=np.complex128,
        )
        rows, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=shared,
            shared_measured_u1_available=True,
            desired_vectors=vectors,
            enabled_now=True,
            covariance=covariance,
            jammer_vector=jammer,
            now_monotonic=10.1 + index / 10.0,
        )
        progresses.append(status["G03"]["transition_progress"])
        assert np.vdot(rows[0], desired) == pytest.approx(
            preserved_response, abs=1e-10
        )

    assert progresses == pytest.approx([index / 10.0 for index in range(1, 11)])
    assert abs(np.vdot(rows[0], jammer)) < 1e-10


def test_per_prn_bank_applies_latest_context_after_active_transition() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired = np.asarray([1.0, 0.7j, -0.3, 0.4 - 0.2j])
    first_jammer = np.asarray([1.0, -0.5j, 0.2 + 0.7j, -0.8])
    latest_jammer = np.asarray([0.3 + 0.4j, -0.7, 1.0j, 0.2])
    desired /= np.linalg.norm(desired)
    first_jammer /= np.linalg.norm(first_jammer)
    latest_jammer /= np.linalg.norm(latest_jammer)
    first_covariance = (
        20.0 * np.outer(first_jammer, np.conj(first_jammer)) + np.eye(4)
    )
    latest_covariance = (
        20.0 * np.outer(latest_jammer, np.conj(latest_jammer)) + np.eye(4)
    )
    vectors = {
        "G03": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        }
    }
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=1.0,
    )
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    preserved_response = np.vdot(baseline[0], desired)

    rows = baseline
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    status = {}
    for index in range(10):
        rows, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=shared,
            shared_measured_u1_available=True,
            desired_vectors=vectors,
            enabled_now=True,
            covariance=first_covariance if index == 0 else latest_covariance,
            jammer_vector=first_jammer if index == 0 else latest_jammer,
            now_monotonic=10.1 + index / 10.0,
        )
        assert np.vdot(rows[0], desired) == pytest.approx(
            preserved_response, abs=1e-10
        )
    assert abs(np.vdot(rows[0], first_jammer)) < 1e-10
    assert abs(np.vdot(rows[0], latest_jammer)) > 1e-3
    assert status["G03"]["lcmv_context_update_pending"] is True

    second_progress = []
    for index in range(10):
        rows, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=shared,
            shared_measured_u1_available=True,
            desired_vectors=vectors,
            enabled_now=True,
            covariance=latest_covariance,
            jammer_vector=latest_jammer,
            now_monotonic=11.1 + index / 10.0,
        )
        second_progress.append(status["G03"]["transition_progress"])
        assert np.vdot(rows[0], desired) == pytest.approx(
            preserved_response, abs=1e-10
        )

    assert second_progress == pytest.approx(
        [index / 10.0 for index in range(1, 11)]
    )
    assert status["G03"]["lcmv_context_update_pending"] is False
    assert abs(np.vdot(rows[0], latest_jammer)) < 1e-10


def test_per_prn_onset_bridge_preserves_full_complex_response() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    desired /= np.linalg.norm(desired)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=32_768,
        transition_s=0.0,
    )
    vectors = {
        "G03": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 100,
        }
    }
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    protected, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        covariance=None,
        jammer_vector=None,
        now_monotonic=10.1,
    )

    assert status["G03"]["source"] == (
        "per_prn_phase_continuous_shared_onset_bridge"
    )
    assert np.vdot(protected[0], desired) == pytest.approx(
        np.vdot(baseline[0], desired), abs=1e-12
    )
    ratios = protected[0] / shared
    assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
    assert status["G03"]["continuity_residual_abs"] < 1e-12


def test_prn_monitor_rolling_window_forgets_an_old_spatial_vector(
    tmp_path: Path,
) -> None:
    fs = 1_023_000.0
    prn = 3
    code = gps_l1_ca_code(prn).astype(np.complex64)
    old = np.asarray([1.0, 0.8j, -0.3, 0.2 - 0.1j])
    new = np.asarray([0.2j, 1.0, 0.6 - 0.2j, -0.7])
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=fs,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: {},
        session_dir=tmp_path,
        session_id="rolling",
        logger=logging.getLogger("test.shared_u1_phase.rolling"),
        satellites=(prn,),
        min_quality_measurements=1,
        vector_window_measurements=4,
    )

    counter = 10_000
    for spatial in (old, old, old, old, new, new, new, new):
        raw = np.zeros((4, 1031), dtype=np.complex64)
        raw[:, 5:1028] = spatial[:, None] * code[None, :]
        monitor._append_span(
            counter - 1028,
            raw,
            np.ones((1, 4), dtype=np.complex128),
        )
        measurement = monitor._correlate(
            {}, "G03", prn, counter, 0.0, 45.0, 0
        )
        assert measurement is not None
        assert measurement["quality_pass"] is True
        counter += 2_000

    recovered = monitor.desired_vectors_snapshot()["G03"]
    assert recovered["quality_pass_count"] == 8
    assert recovered["rolling_quality_measurement_count"] == 4
    assert phase_invariant_coherence(
        recovered["desired_spatial_vector"], new
    ) > 1.0 - 1e-12


@pytest.mark.parametrize(
    "retirement_snapshot",
    [
        {"tracking_monitor": [], "prns": []},
        _monitor_snapshot(3, state="lost"),
    ],
    ids=("snapshot_gap", "explicit_lost_state"),
)
def test_prn_monitor_same_source_reacquisition_starts_a_fresh_epoch(
    tmp_path: Path,
    retirement_snapshot: dict[str, object],
) -> None:
    current_snapshot = _monitor_snapshot(3)
    old = np.asarray([1.0, 0.8j, -0.3, 0.2 - 0.1j])
    new = np.asarray([0.2j, 1.0, 0.6 - 0.2j, -0.7])
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=1_023_000.0,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: current_snapshot,
        session_dir=tmp_path,
        session_id="same-prn-new-epoch",
        logger=logging.getLogger("test.shared_u1_phase.same_prn_epoch"),
        satellites=(3,),
        min_quality_measurements=2,
        vector_window_measurements=4,
    )
    _add_monitor_quality_measurement(
        monitor, prn=3, spatial=old, counter=10_000
    )
    _add_monitor_quality_measurement(
        monitor, prn=3, spatial=old, counter=12_000
    )
    assert monitor.desired_vectors_snapshot()["G03"]["quality_pass_count"] == 2

    # Establish source ownership and the duplicate-counter marker without
    # perturbing the already-built projector aggregate.
    original_correlate = monitor._correlate
    observed: list[tuple[str, int, int]] = []

    def observe_correlation(
        entry: dict[str, object],
        satellite: str,
        prn: int,
        counter: int,
        doppler_hz: float,
        cno_db_hz: float,
        source_index: int,
    ) -> dict[str, object]:
        del entry, prn, doppler_hz, cno_db_hz
        observed.append((satellite, counter, source_index))
        return {"event": "test_tracking_measurement"}

    monitor._correlate = observe_correlation  # type: ignore[method-assign]
    monitor._measure_tracking()
    assert observed == [("G03", 500, 0)]
    assert monitor._last_counter == {"G03": 500}

    current_snapshot = retirement_snapshot
    monitor._measure_tracking()
    assert monitor.desired_vectors_snapshot() == {}
    assert "G03" not in monitor._aggregates
    assert "G03" not in monitor._last_counter
    assert monitor._source_satellites == {}

    # The same receiver counter is accepted after reacquisition because the
    # deduplication marker belongs to the retired epoch.
    current_snapshot = _monitor_snapshot(3)
    monitor._measure_tracking()
    assert observed[-1] == ("G03", 500, 0)
    assert len(observed) == 2

    # Only post-reacquisition projectors may contribute.  One new sample is
    # insufficient to publish; the second publishes a pure new-vector result.
    monitor._correlate = original_correlate  # type: ignore[method-assign]
    first = _add_monitor_quality_measurement(
        monitor, prn=3, spatial=new, counter=30_000
    )
    assert first["aggregate_quality_pass_count"] == 1
    assert monitor.desired_vectors_snapshot() == {}
    second = _add_monitor_quality_measurement(
        monitor, prn=3, spatial=new, counter=32_000
    )
    assert second["aggregate_quality_pass_count"] == 2
    recovered = monitor.desired_vectors_snapshot()["G03"]
    assert recovered["quality_pass_count"] == 2
    assert recovered["rolling_quality_measurement_count"] == 2
    assert phase_invariant_coherence(
        recovered["desired_spatial_vector"], new
    ) > 1.0 - 1e-12


def test_prn_monitor_source_reassignment_retires_the_previous_prn(
    tmp_path: Path,
) -> None:
    current_snapshot = _monitor_snapshot(3)
    first = np.asarray([1.0, 0.8j, -0.3, 0.2 - 0.1j])
    second = np.asarray([0.2j, 1.0, 0.6 - 0.2j, -0.7])
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=1_023_000.0,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: current_snapshot,
        session_dir=tmp_path,
        session_id="source-reassignment",
        logger=logging.getLogger("test.shared_u1_phase.source_reassignment"),
        satellites=(),
        source_count=1,
        min_quality_measurements=1,
    )
    _add_monitor_quality_measurement(
        monitor, prn=3, spatial=first, counter=10_000
    )

    original_correlate = monitor._correlate
    monitor._correlate = (  # type: ignore[method-assign]
        lambda *args, **kwargs: {"event": "test_tracking_measurement"}
    )
    monitor._measure_tracking()
    assert monitor._source_satellites == {0: "G03"}
    assert monitor._last_counter == {"G03": 500}

    current_snapshot = _monitor_snapshot(4)
    monitor._measure_tracking()
    assert monitor._source_satellites == {0: "G04"}
    assert "G03" not in monitor._aggregates
    assert "G03" not in monitor._last_counter
    assert "G03" not in monitor.desired_vectors_snapshot()

    monitor._correlate = original_correlate  # type: ignore[method-assign]
    measurement = _add_monitor_quality_measurement(
        monitor, prn=4, spatial=second, counter=30_000
    )
    assert measurement["aggregate_quality_pass_count"] == 1
    assert set(monitor.desired_vectors_snapshot()) == {"G04"}


def test_per_prn_bank_rejects_an_expired_published_vector() -> None:
    common = np.ones((4,), dtype=np.complex128)
    desired = np.asarray([1.0, 0.8j, -0.3, 0.2 - 0.1j])
    bank = PerPrnMeasuredVectorBeamformerBank(
        satellites=(),
        source_count=1,
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=0.0,
    )
    rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors={
            "G03": {
                "desired_spatial_vector": desired,
                "updated_monotonic": 10.0,
                "tracking_sample_counter": 100,
            }
        },
        source_satellites=(3,),
        enabled_now=False,
        now_monotonic=20.0,
        max_vector_age_s=2.5,
    )

    np.testing.assert_allclose(rows[0], common)
    assert status["G03"]["desired_vector_available"] is False
    assert status["G03"]["tracking_sample_counter"] is None


def test_prn_monitor_randomized_assignment_epochs_never_leak_state(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(0xA11CE)
    current_snapshot: dict[str, object] = {
        "tracking_monitor": [],
        "prns": [],
    }
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=1_023_000.0,
        channel_count=4,
        phase_correction_vector=None,
        tracking_snapshot=lambda: current_snapshot,
        session_dir=tmp_path,
        session_id="randomized-epochs",
        logger=logging.getLogger("test.shared_u1_phase.randomized_epochs"),
        satellites=(),
        source_count=1,
        min_quality_measurements=1,
    )
    previous_satellite: str | None = None
    expected_satellite: str | None = None
    expected_epoch = 0
    new_epoch = False
    fake_calls = 0
    previous_counter: int | None = None
    counter_reset_epochs = 0

    def publish_epoch_marker(
        entry: dict[str, object],
        satellite: str,
        prn: int,
        counter: int,
        doppler_hz: float,
        cno_db_hz: float,
        source_index: int,
    ) -> dict[str, object]:
        nonlocal fake_calls
        del entry, prn, doppler_hz, cno_db_hz
        fake_calls += 1
        assert satellite == expected_satellite
        assert source_index == 0
        if new_epoch:
            # Reconciliation runs before correlation.  Any object from an
            # earlier ownership epoch must already be gone, including when
            # the same PRN has returned to the same source.
            assert satellite not in monitor._aggregates
            assert satellite not in monitor._last_counter
            assert satellite not in monitor.desired_vectors_snapshot()
        else:
            aggregate = monitor._aggregates.get(satellite)
            if aggregate is not None:
                assert aggregate["epoch"] == expected_epoch
        monitor._aggregates[satellite] = {  # type: ignore[assignment]
            "epoch": expected_epoch,
            "source_index": source_index,
        }
        with monitor._vectors_lock:
            monitor._latest_vectors[satellite] = {
                "desired_spatial_vector": np.full(
                    (4,), complex(expected_epoch, 0.0), dtype=np.complex128
                ),
                "updated_monotonic": float(expected_epoch),
                "tracking_sample_counter": counter,
                "test_epoch": expected_epoch,
            }
        return {"event": "test_tracking_measurement"}

    monitor._correlate = publish_epoch_marker  # type: ignore[method-assign]
    counter = 1
    for _ in range(2_000):
        event = int(rng.integers(0, 5))
        if event == 0:
            # Complete snapshot gap.
            current_snapshot = {"tracking_monitor": [], "prns": []}
            expected_satellite = None
        elif event == 1 and previous_satellite is not None:
            # A stale monitor record contradicted by authoritative lost state.
            lost_prn = int(previous_satellite[1:])
            current_snapshot = _monitor_snapshot(
                lost_prn, counter=counter, state="lost"
            )
            expected_satellite = None
        else:
            prn = 3 if event in {1, 2} else 4
            # Exercise normal increments, exact duplicates, and counter resets.
            counter = (
                int(rng.integers(1, 8))
                if int(rng.integers(0, 4)) == 0
                else counter + int(rng.integers(0, 5))
            )
            current_snapshot = _monitor_snapshot(prn, counter=counter)
            expected_satellite = f"G{prn:02d}"

        new_epoch = bool(
            expected_satellite is not None
            and (
                expected_satellite != previous_satellite
                or (
                    previous_counter is not None
                    and counter < previous_counter
                )
            )
        )
        if new_epoch:
            expected_epoch += 1
            if (
                expected_satellite == previous_satellite
                and previous_counter is not None
                and counter < previous_counter
            ):
                counter_reset_epochs += 1
        monitor._measure_tracking()

        expected_mapping = (
            {0: expected_satellite}
            if expected_satellite is not None
            else {}
        )
        assert monitor._source_satellites == expected_mapping
        expected_keys = (
            {expected_satellite}
            if expected_satellite is not None
            else set()
        )
        assert set(monitor._aggregates) == expected_keys
        assert set(monitor._last_counter) == expected_keys
        assert set(monitor.desired_vectors_snapshot()) == expected_keys
        if expected_satellite is not None:
            assert (
                monitor._aggregates[expected_satellite]["epoch"]
                == expected_epoch
            )
            assert (
                monitor.desired_vectors_snapshot()[expected_satellite][
                    "test_epoch"
                ]
                == expected_epoch
            )
        previous_satellite = expected_satellite
        previous_counter = counter if expected_satellite is not None else None

    assert fake_calls > 500
    assert counter_reset_epochs > 50


def test_per_prn_module_contains_independent_covariance_solver() -> None:
    source = Path(
        "src/antijamming/gnss/shared_u1_phase_compensation.py"
    ).read_text(encoding="utf-8")
    assert "covariance_lcmv_vector_null_weights" in source
    assert "class PerPrnMeasuredVectorBeamformerBank" in source
