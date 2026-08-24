"""Background cold-start spatial acquisition for jammer-on receiver startup."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import queue
import threading
import time
from typing import Callable

import numpy as np

from antijamming.dsp.frequency_notch import StatefulComplexBandstop
from antijamming.gnss.cold_start_acquisition import (
    SpatialPcpsResult,
    evaluate_spatial_pcps_consistency,
    noncoherent_spatial_pcps,
)


def orthogonal_nullspace_rows(null_vectors: np.ndarray) -> np.ndarray:
    """Return a nonredundant, equal-noise-gain jammer-null basis."""

    vectors = np.asarray(null_vectors, dtype=np.complex128)
    if vectors.ndim == 1:
        vectors = vectors[:, None]
    if vectors.ndim != 2 or not 0 < vectors.shape[1] < vectors.shape[0]:
        raise ValueError("null-vector matrix must leave at least one spatial DOF")
    left, singular_values, _ = np.linalg.svd(vectors, full_matrices=True)
    tolerance = (
        max(vectors.shape)
        * np.finfo(np.float64).eps
        * max(float(singular_values[0]), 1.0)
    )
    rank = int(np.count_nonzero(singular_values > tolerance))
    if rank != vectors.shape[1]:
        raise ValueError("null-vector matrix is rank deficient")
    return np.asarray(
        np.sqrt(vectors.shape[0]) * left[:, rank:].T,
        dtype=np.complex128,
    )


class ColdStartSpatialAcquisitionMonitor:
    """Acquire PRNs before tracking and publish only physically consistent U1.

    This worker never touches the lossless FIFO writer.  The writer submits an
    already-owned raw chunk with its absolute sample counter; the worker keeps
    only recent separated observations and performs array PCPS off the hot
    path.  A PRN vector is published only after repeated Doppler, code/carrier,
    and spatial-vector consistency checks.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        channel_count: int,
        phase_correction_vector: tuple[complex, ...] | None,
        context_snapshot: Callable[[], dict[str, object]],
        session_dir: Path,
        session_id: str,
        logger: logging.Logger,
        measurement_interval_s: float = 1.0,
        dwell_count: int = 5,
        min_observations: int = 4,
        history_observations: int = 6,
        notch_bandwidth_hz: float = 600_000.0,
        notch_fir_taps: int = 257,
    ) -> None:
        self._sample_rate_hz = float(sample_rate_hz)
        self._channel_count = int(channel_count)
        correction = phase_correction_vector or tuple(
            1.0 + 0.0j for _ in range(self._channel_count)
        )
        self._correction = np.asarray(correction, dtype=np.complex64).reshape(-1)
        if self._correction.size != self._channel_count:
            raise ValueError("cold-start acquisition correction length mismatch")
        self._context_snapshot = context_snapshot
        self._session_id = str(session_id)
        self._logger = logger
        self._measurement_interval_s = max(0.25, float(measurement_interval_s))
        self._dwell_count = max(1, int(dwell_count))
        self._min_observations = max(2, int(min_observations))
        self._history_observations = max(
            self._min_observations, int(history_observations)
        )
        self._notch_bandwidth_hz = float(notch_bandwidth_hz)
        self._notch_fir_taps = int(notch_fir_taps)
        self._path = Path(session_dir) / "cold_start_spatial_acquisition.jsonl"
        self._queue: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue(
            maxsize=8
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._handle = None
        self._last_evaluation_monotonic = float("-inf")
        self._histories: dict[int, deque[tuple[int, SpatialPcpsResult]]] = {
            prn: deque(maxlen=self._history_observations)
            for prn in range(1, 33)
        }
        self._vectors_lock = threading.Lock()
        self._latest_vectors: dict[str, dict[str, object]] = {}
        self._last_projector: np.ndarray | None = None
        self._last_notch_offset_hz: float | None = None
        self._last_context_mode: str | None = None
        self._context_evaluation_count = 0
        self._last_accepted_prns: tuple[int, ...] = ()
        self._submit_count = 0
        self._latest_only_replacements = 0
        self._evaluation_count = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8", buffering=1)
        self._thread = threading.Thread(
            target=self._run,
            name="gnss_cold_start_spatial_acquisition",
            daemon=True,
        )
        self._thread.start()
        self._write(
            {
                "event": "cold_start_spatial_acquisition_start",
                "sample_rate_hz": self._sample_rate_hz,
                "dwell_count": self._dwell_count,
                "min_observations": self._min_observations,
                "measurement_interval_s": self._measurement_interval_s,
                "decision": (
                    "common-hypothesis noncoherent spatial PCPS plus repeated "
                    "Doppler/code-carrier/spatial-vector consistency"
                ),
            }
        )

    def submit(self, sample_start: int, raw_chunk: np.ndarray) -> None:
        self._submit_count += 1
        item = (
            int(sample_start),
            np.asarray(raw_chunk, dtype=np.complex64),
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._latest_only_replacements += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except (queue.Empty, queue.Full):
                pass

    def desired_vectors_snapshot(self) -> dict[str, dict[str, object]]:
        with self._vectors_lock:
            return {
                satellite: {
                    **payload,
                    "desired_spatial_vector": np.array(
                        payload["desired_spatial_vector"], copy=True
                    ),
                }
                for satellite, payload in self._latest_vectors.items()
            }

    def classification_snapshot(self) -> dict[str, object]:
        """Return the GNSS veto state for a broadband-source candidate."""

        with self._vectors_lock:
            return {
                "context_mode": self._last_context_mode,
                "evaluation_count": int(self._context_evaluation_count),
                "required_evaluations": int(self._min_observations),
                "sufficient_observations": bool(
                    self._last_context_mode == "classify"
                    and self._context_evaluation_count >= self._min_observations
                ),
                "accepted_prns": list(self._last_accepted_prns),
                "gnss_consistency_veto": bool(self._last_accepted_prns),
            }

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
        thread_stopped = bool(thread is None or not thread.is_alive())
        if not thread_stopped:
            self._logger.error(
                "Cold-start spatial acquisition worker did not stop within 3 s"
            )
        self._thread = None
        self._write(
            {
                "event": "cold_start_spatial_acquisition_stop",
                "submit_count": self._submit_count,
                "latest_only_replacements": self._latest_only_replacements,
                "evaluation_count": self._evaluation_count,
                "thread_stopped": thread_stopped,
            }
        )
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                return
            # The acquisition worker is deliberately latest-only.  Absolute
            # counters retain timing provenance across skipped chunks.
            while True:
                try:
                    newer = self._queue.get_nowait()
                except queue.Empty:
                    break
                if newer is None:
                    return
                self._latest_only_replacements += 1
                item = newer
            now = time.monotonic()
            if now - self._last_evaluation_monotonic < self._measurement_interval_s:
                continue
            self._last_evaluation_monotonic = now
            try:
                self._evaluate(item[0], item[1], now)
            except InterruptedError:
                return
            except Exception as exc:
                self._logger.warning(
                    "Cold-start spatial acquisition evaluation failed: %s", exc
                )

    def _evaluate(self, sample_start: int, raw: np.ndarray, now: float) -> None:
        context = self._context_snapshot()
        active = bool(context.get("active", False))
        if not active:
            self._clear("cold-start jammer rescue inactive")
            return
        mode = str(context.get("mode", "rescue")).strip().lower()
        if mode not in {"classify", "rescue"}:
            raise ValueError(f"unknown cold-start acquisition mode: {mode}")
        jammer_vectors = np.asarray(
            context.get("jammer_vectors", []), dtype=np.complex128
        )
        if mode == "classify":
            # A coherent broadband GNSS simulator is rank one too. Search the
            # un-nulled sensors first; a physically consistent PRN vetoes
            # automatic broadband-jammer activation.
            rows = np.eye(self._channel_count, dtype=np.complex128)
            projector = np.eye(self._channel_count, dtype=np.complex128)
        else:
            rows = orthogonal_nullspace_rows(jammer_vectors)
            basis = np.asarray(
                np.linalg.qr(jammer_vectors, mode="reduced")[0],
                dtype=np.complex128,
            )
            projector = np.eye(self._channel_count) - basis @ basis.conj().T
        notch_value = context.get("notch_frequency_offset_hz")
        notch_offset_hz = (
            float(notch_value) if notch_value is not None else None
        )
        context_changed = bool(
            self._last_projector is not None
            and np.linalg.norm(projector - self._last_projector) > 0.15
        )
        context_changed = context_changed or bool(
            self._last_context_mode is not None
            and mode != self._last_context_mode
        )
        context_changed = context_changed or bool(
            self._last_notch_offset_hz is not None
            and (
                notch_offset_hz is None
                or abs(notch_offset_hz - self._last_notch_offset_hz)
                > 0.25 * self._notch_bandwidth_hz
            )
        )
        if context_changed:
            self._clear("jammer subspace, mode, or notch changed")
        self._last_projector = np.array(projector, copy=True)
        self._last_notch_offset_hz = notch_offset_hz
        self._last_context_mode = mode

        calibrated = np.asarray(
            raw * self._correction[:, None], dtype=np.complex64
        )
        discard = 0
        filtered = calibrated
        if notch_offset_hz is not None:
            notch = StatefulComplexBandstop(
                sample_rate_hz=self._sample_rate_hz,
                bandwidth_hz=self._notch_bandwidth_hz,
                num_taps=self._notch_fir_taps,
            )
            notch.tune(notch_offset_hz)
            filtered = notch.process(calibrated)
            discard = max(2 * notch.group_delay_samples, notch.num_taps)
            filtered = filtered[:, discard:]
        effective_start = int(sample_start) + discard
        results = noncoherent_spatial_pcps(
            filtered,
            rows,
            sample_rate_hz=self._sample_rate_hz,
            prns=tuple(range(1, 33)),
            dwell_count=self._dwell_count,
            cancel_requested=self._stop_event.is_set,
        )
        self._evaluation_count += 1
        accepted: dict[str, dict[str, object]] = {}
        decisions: list[dict[str, object]] = []
        by_prn = {result.prn: result for result in results}
        for prn in range(1, 33):
            history = self._histories[prn]
            history.append((effective_start, by_prn[prn]))
            decision = evaluate_spatial_pcps_consistency(
                tuple(history)[-self._min_observations :],
                sample_rate_hz=self._sample_rate_hz,
                min_observations=self._min_observations,
            )
            if decision.accepted:
                accepted[f"G{prn:02d}"] = {
                    "desired_spatial_vector": np.array(
                        decision.latest_projected_spatial_vector, copy=True
                    ),
                    "updated_monotonic": now,
                    "tracking_sample_counter": int(
                        effective_start
                        + self._dwell_count
                        * int(round(self._sample_rate_hz / 1000.0))
                    ),
                    "vector_source": "cold_start_spatial_pcps",
                    "quality_pass": True,
                    "aggregate_quality_pass_count": decision.observation_count,
                    "peak_to_median_db": decision.min_peak_to_median_db,
                    "doppler_span_hz": decision.doppler_span_hz,
                    "code_phase_slope_error_samples_per_s": (
                        decision.code_phase_slope_error_samples_per_s
                    ),
                    "code_phase_fit_max_residual_samples": (
                        decision.code_phase_fit_max_residual_samples
                    ),
                    "projected_vector_coherence": (
                        decision.min_projected_vector_coherence
                    ),
                    "spatial_projector_dominant_fraction": (
                        decision.min_spatial_projector_dominant_fraction
                    ),
                }
            decisions.append(
                {
                    "prn": prn,
                    "accepted": decision.accepted,
                    "reasons": list(decision.reasons),
                    "min_peak_to_median_db": self._finite_or_none(
                        decision.min_peak_to_median_db
                    ),
                    "doppler_span_hz": self._finite_or_none(
                        decision.doppler_span_hz
                    ),
                    "code_phase_slope_error_samples_per_s": (
                        self._finite_or_none(
                            decision.code_phase_slope_error_samples_per_s
                        )
                    ),
                    "code_phase_fit_max_residual_samples": (
                        self._finite_or_none(
                            decision.code_phase_fit_max_residual_samples
                        )
                    ),
                    "min_projected_vector_coherence": (
                        self._finite_or_none(
                            decision.min_projected_vector_coherence
                        )
                    ),
                }
            )
        with self._vectors_lock:
            self._context_evaluation_count += 1
            self._latest_vectors = accepted
            self._last_context_mode = mode
            self._last_accepted_prns = tuple(
                sorted(int(value[1:]) for value in accepted)
            )
        ranked = sorted(
            decisions,
            key=lambda value: float(value["min_peak_to_median_db"] or -1e300),
            reverse=True,
        )
        self._write(
            {
                "event": "cold_start_spatial_acquisition_evaluation",
                "evaluation_index": self._evaluation_count,
                "raw_sample_start": int(sample_start),
                "effective_sample_start": effective_start,
                "context_mode": mode,
                "interference_rank": int(jammer_vectors.shape[1]),
                "notch_frequency_offset_hz": notch_offset_hz,
                "accepted_prns": [int(value[1:]) for value in accepted],
                "top_decisions": ranked[:10],
            }
        )

    def _clear(self, reason: str) -> None:
        had_state = any(self._histories[prn] for prn in self._histories)
        for history in self._histories.values():
            history.clear()
        with self._vectors_lock:
            had_state = had_state or bool(self._latest_vectors)
            self._latest_vectors = {}
            self._context_evaluation_count = 0
            self._last_accepted_prns = ()
        self._last_projector = None
        self._last_notch_offset_hz = None
        self._last_context_mode = None
        if had_state:
            self._write(
                {
                    "event": "cold_start_spatial_acquisition_reset",
                    "reason": str(reason),
                }
            )

    @staticmethod
    def _finite_or_none(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if np.isfinite(number) else None

    def _write(self, payload: dict[str, object]) -> None:
        if self._handle is None:
            return
        wall_time_ns = time.time_ns()
        record = {
            "session_id": self._session_id,
            "timestamp_utc": datetime.fromtimestamp(
                wall_time_ns / 1e9, timezone.utc
            ).isoformat(),
            "timestamp_local": datetime.fromtimestamp(
                wall_time_ns / 1e9
            ).astimezone().isoformat(),
            "wall_time_unix_ns": wall_time_ns,
            "monotonic_ns": time.monotonic_ns(),
            **payload,
        }
        try:
            self._handle.write(
                json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n"
            )
        except (OSError, TypeError, ValueError) as exc:
            self._logger.warning(
                "Cold-start spatial acquisition log write failed: %s", exc
            )


__all__ = [
    "ColdStartSpatialAcquisitionMonitor",
    "orthogonal_nullspace_rows",
]
