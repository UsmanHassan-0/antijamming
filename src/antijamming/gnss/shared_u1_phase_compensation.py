"""Measured-vector GNSS beamforming with per-PRN response continuity.

Every GNSS-SDR source uses the same spatial LCMV weight vector.  A complex
scalar is applied per tracked PRN so switching to that shared vector preserves
the PRN's previous complex array response.  Scalar multiplication cannot move
or weaken the shared spatial null.

The legacy shared-row bank remains for regression comparison.  The runtime uses
``PerPrnMeasuredVectorBeamformerBank`` so every mapped PRN receives its own
measured-vector spatial row.  Complex-response continuity is a constraint on
each spatial update; it is not used as a substitute for per-PRN beamforming.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from antijamming.dsp.beamforming.lcmv import (
    covariance_lcmv_vector_null_weights,
)


GPS_L1_HZ = 1_575_420_000.0
GPS_CA_RATE_HZ = 1_023_000.0
GPS_CA_LENGTH = 1023


def gps_l1_ca_code(prn: int) -> np.ndarray:
    """Return the +/-1 GPS L1 C/A sequence used by GNSS-SDR."""

    delays = (
        5, 6, 7, 8, 17, 18, 139, 140, 141, 251, 252, 254, 255, 256,
        257, 258, 469, 470, 471, 472, 473, 474, 509, 512, 513, 514,
        515, 516, 859, 860, 861, 862,
    )
    if not 1 <= int(prn) <= 32:
        raise ValueError(f"GPS L1 C/A PRN must be in 1..32, got {prn}")
    g1_register = [True] * 10
    g2_register = [True] * 10
    g1 = [False] * GPS_CA_LENGTH
    g2 = [False] * GPS_CA_LENGTH
    for index in range(GPS_CA_LENGTH):
        g1[index] = g1_register[0]
        g2[index] = g2_register[0]
        feedback1 = g1_register[7] ^ g1_register[0]
        feedback2 = (
            g2_register[8]
            ^ g2_register[7]
            ^ g2_register[4]
            ^ g2_register[2]
            ^ g2_register[1]
            ^ g2_register[0]
        )
        g1_register[:-1] = g1_register[1:]
        g2_register[:-1] = g2_register[1:]
        g1_register[9] = feedback1
        g2_register[9] = feedback2
    delay = GPS_CA_LENGTH - delays[int(prn) - 1]
    code = np.empty((GPS_CA_LENGTH,), dtype=np.float32)
    for index in range(GPS_CA_LENGTH):
        code[index] = 1.0 if (g1[index] ^ g2[delay]) else -1.0
        delay = (delay + 1) % GPS_CA_LENGTH
    return code


def phase_invariant_coherence(a: np.ndarray, b: np.ndarray) -> float:
    left = np.asarray(a, dtype=np.complex128).reshape(-1)
    right = np.asarray(b, dtype=np.complex128).reshape(-1)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= np.finfo(np.float64).tiny:
        return 0.0
    return float(
        np.clip(abs(np.vdot(left, right)) ** 2 / denominator**2, 0.0, 1.0)
    )


def apply_shared_phase_fanout(
    raw_channels: np.ndarray,
    logical_rows: np.ndarray,
    phase_correction: np.ndarray,
) -> tuple[np.ndarray, bool]:
    """Apply shared spatial rows, using one beamformer when rows are collinear.

    The GNSS convention is ``y = (conj(w) * correction) @ x``.  If every row
    is ``gamma_i * w_shared``, then every output is exactly
    ``conj(gamma_i) * y_shared``.  During a non-collinear recovery transition
    the function automatically uses the general matrix product.
    """

    source = np.asarray(raw_channels, dtype=np.complex64)
    rows = np.asarray(logical_rows, dtype=np.complex128)
    correction = np.asarray(phase_correction, dtype=np.complex64).reshape(-1)
    if source.ndim != 2 or rows.ndim != 2:
        raise ValueError("shared phase fanout expects 2-D rows and raw channels")
    if source.shape[0] != rows.shape[1] or correction.size != source.shape[0]:
        raise ValueError("shared phase fanout channel dimensions do not match")
    if rows.shape[0] == 0 or source.shape[1] == 0:
        return np.zeros((rows.shape[0], source.shape[1]), dtype=np.complex64), True

    base = rows[0]
    base_power = float(np.vdot(base, base).real)
    if base_power > np.finfo(np.float64).tiny:
        scales = np.asarray(rows @ np.conj(base) / base_power, dtype=np.complex128)
        reconstructed = scales[:, None] * base[None, :]
        if np.allclose(rows, reconstructed, rtol=1e-7, atol=1e-9):
            effective_base = np.asarray(
                np.conj(base) * correction,
                dtype=np.complex64,
            )
            shared_output = np.asarray(
                effective_base @ source,
                dtype=np.complex64,
            )
            outputs = np.conj(scales).astype(np.complex64)[:, None] * shared_output
            return np.ascontiguousarray(outputs, dtype=np.complex64), True

    effective = np.asarray(
        np.conj(rows) * correction[None, :],
        dtype=np.complex64,
    )
    return np.ascontiguousarray(effective @ source, dtype=np.complex64), False


def _align_common_phase(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.complex128).reshape(-1)
    if values.size == 0:
        return values
    reference = int(np.argmax(np.abs(values)))
    return values * np.exp(-1j * np.angle(values[reference]))


def _complex_payload(vector: np.ndarray) -> dict[str, list[float]]:
    values = np.asarray(vector, dtype=np.complex128).reshape(-1)
    return {
        "real": [float(value.real) for value in values],
        "imag": [float(value.imag) for value in values],
        "magnitude": [float(abs(value)) for value in values],
        "phase_deg": [float(np.degrees(np.angle(value))) for value in values],
    }


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(slots=True)
class _RawSpan:
    start: int
    end: int
    raw: np.ndarray
    logical_weights: np.ndarray


@dataclass(slots=True)
class _PrnAggregate:
    total_count: int
    projectors: deque[np.ndarray]
    source_index: int


@dataclass(slots=True)
class _PhaseState:
    satellite: str | None
    current: np.ndarray
    start: np.ndarray
    target: np.ndarray
    desired: np.ndarray | None = None
    total_chunks: int = 0
    completed_chunks: int = 0
    source: str = "shared_common_weights"
    scale: complex = 1.0 + 0.0j
    requested_amplitude_compensation_db: float | None = None
    continuity_residual_abs: float | None = None
    guard_reason: str | None = None
    post_onset_vector: np.ndarray | None = None
    post_onset_pass_count: int = 0
    last_quality_counter: int | None = None


@dataclass(slots=True)
class _PerPrnState:
    satellite: str
    current: np.ndarray
    start: np.ndarray
    target: np.ndarray
    desired: np.ndarray | None = None
    source: str = "uniform_waiting_for_prn_vector"
    total_chunks: int = 0
    completed_chunks: int = 0
    last_quality_counter: int | None = None
    desired_updated_monotonic: float | None = None
    guard_reason: str | None = None
    continuity_residual_abs: float | None = None
    desired_step_deg: float | None = None
    lcmv_null_residual_abs: float | None = None
    lcmv_condition_number: float | None = None
    lcmv_weight_norm: float | None = None
    last_lcmv_context_generation: int | None = None


class SharedU1PhaseCompensationBank:
    """Fan out one shared LCMV null with complex per-PRN continuity."""

    def __init__(
        self,
        *,
        satellites: tuple[int, ...],
        channel_count: int,
        sample_rate_hz: float,
        samples_per_chunk: int,
        transition_s: float,
        source_count: int | None = None,
        max_weight_norm: float = 8.0,
        min_post_onset_desired_updates: int = 3,
        max_post_onset_desired_step_deg: float = 10.0,
    ) -> None:
        self._satellites = tuple(int(value) for value in satellites)
        self._source_count = (
            len(self._satellites)
            if self._satellites
            else max(1, int(source_count or 0))
        )
        self._channel_count = int(channel_count)
        chunk_s = max(1, int(samples_per_chunk)) / max(1.0, float(sample_rate_hz))
        self._transition_chunks = (
            0
            if float(transition_s) <= 0.0
            else max(1, int(math.ceil(float(transition_s) / chunk_s)))
        )
        self._max_weight_norm = max(float(max_weight_norm), 1e-6)
        self._min_post_onset_updates = max(2, int(min_post_onset_desired_updates))
        self._max_post_onset_step_deg = max(
            0.1, min(float(max_post_onset_desired_step_deg), 90.0)
        )
        self._states: dict[int, _PhaseState] = {}
        self._enabled_previous = False
        self._activation_monotonic: float | None = None
        self._last_shared_protection: np.ndarray | None = None

    @staticmethod
    def _valid_vector(value: object, size: int) -> np.ndarray | None:
        vector = np.asarray(
            value if value is not None else [], dtype=np.complex128
        ).reshape(-1)
        if (
            vector.size != int(size)
            or not np.all(np.isfinite(vector))
            or float(np.linalg.norm(vector)) <= np.finfo(np.float64).tiny
        ):
            return None
        return vector / float(np.linalg.norm(vector))

    @staticmethod
    def _align_to_reference(
        desired: np.ndarray,
        reference: np.ndarray | None,
    ) -> np.ndarray:
        vector = np.asarray(desired, dtype=np.complex128).reshape(-1)
        if reference is None:
            return _align_common_phase(vector)
        overlap = complex(np.vdot(reference, vector))
        if abs(overlap) <= np.finfo(np.float64).tiny:
            return _align_common_phase(vector)
        return vector * np.exp(-1j * np.angle(overlap))

    def _continuous_target(
        self,
        *,
        start: np.ndarray,
        requested: np.ndarray,
        desired: np.ndarray,
    ) -> tuple[np.ndarray | None, dict[str, object]]:
        """Preserve the established PRN's full complex response."""

        old_response = complex(np.vdot(start, desired))
        raw_response = complex(np.vdot(requested, desired))
        response_floor = 1e-8 * max(
            1.0, float(np.linalg.norm(requested) * np.linalg.norm(desired))
        )
        metrics: dict[str, object] = {
            "preserved_response": old_response,
            "uncompensated_target_response": raw_response,
            "scale": 1.0 + 0.0j,
            "requested_amplitude_compensation_db": None,
            "continuity_residual_abs": None,
            "guard_reason": None,
        }
        if abs(raw_response) <= response_floor:
            metrics["guard_reason"] = "shared_target_prn_response_near_zero"
            return None, metrics
        # np.vdot(gamma*w, a) = conj(gamma) * np.vdot(w, a).
        exact_scale = complex(np.conj(old_response / raw_response))
        metrics["requested_amplitude_compensation_db"] = 20.0 * math.log10(
            max(abs(exact_scale), 1e-300)
        )
        # A scalar cannot move or fill a spatial null.  Retaining the full
        # complex scale therefore preserves the established tracking-loop
        # amplitude and phase while leaving the shared spatial pattern intact.
        # The norm guard below rejects an invalid target instead of silently
        # weakening the desired PRN with a phase-only approximation.
        scale = exact_scale
        metrics["scale"] = scale
        target = np.asarray(scale * requested, dtype=np.complex128)
        if (
            not np.all(np.isfinite(target))
            or float(np.linalg.norm(target)) > self._max_weight_norm
        ):
            metrics["guard_reason"] = "phase_compensated_weight_norm_exceeded"
            return None, metrics
        metrics["continuity_residual_abs"] = float(
            abs(np.vdot(target, desired) - old_response)
        )
        return target, metrics

    def _start_transition(
        self,
        state: _PhaseState,
        *,
        requested: np.ndarray,
        source: str,
        immediate: bool,
    ) -> None:
        if state.desired is None:
            state.guard_reason = "PRN desired vector unavailable"
            return
        target, metrics = self._continuous_target(
            start=state.current,
            requested=requested,
            desired=state.desired,
        )
        state.guard_reason = str(metrics.get("guard_reason") or "") or None
        state.scale = complex(metrics.get("scale", 1.0 + 0.0j))
        state.requested_amplitude_compensation_db = _finite_float(
            metrics.get("requested_amplitude_compensation_db")
        )
        state.continuity_residual_abs = _finite_float(
            metrics.get("continuity_residual_abs")
        )
        if target is None:
            return
        state.start = np.array(state.current, copy=True)
        state.target = np.array(target, copy=True)
        state.source = str(source)
        state.total_chunks = 1 if immediate else self._transition_chunks
        state.completed_chunks = 0
        if state.total_chunks == 0:
            state.current = np.array(state.target, copy=True)

    @staticmethod
    def _advance_transition(state: _PhaseState) -> None:
        if state.total_chunks <= 0 or state.completed_chunks >= state.total_chunks:
            return
        state.completed_chunks += 1
        alpha = state.completed_chunks / float(state.total_chunks)
        state.current = np.asarray(
            (1.0 - alpha) * state.start + alpha * state.target,
            dtype=np.complex128,
        )

    def advance(
        self,
        *,
        shared_common_weights: np.ndarray,
        shared_measured_u1_weights: np.ndarray,
        shared_measured_u1_available: bool,
        desired_vectors: dict[str, dict[str, object]],
        source_satellites: tuple[int | None, ...] | None = None,
        enabled_now: bool,
        now_monotonic: float | None = None,
        max_vector_age_s: float = 2.5,
        emit_status: bool = True,
    ) -> tuple[np.ndarray, dict[str, object]]:
        common = np.asarray(shared_common_weights, dtype=np.complex128).reshape(-1)
        protection = np.asarray(
            shared_measured_u1_weights, dtype=np.complex128
        ).reshape(-1)
        if common.size != self._channel_count or protection.size != self._channel_count:
            raise ValueError("shared phase-compensation weight length mismatch")
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        enabled = bool(enabled_now)
        rising = enabled and not self._enabled_previous
        falling = not enabled and self._enabled_previous
        if rising:
            self._activation_monotonic = now
        elif falling:
            self._activation_monotonic = None
        self._enabled_previous = enabled
        protection_changed = bool(
            shared_measured_u1_available
            and (
                self._last_shared_protection is None
                or not np.allclose(
                    self._last_shared_protection,
                    protection,
                    rtol=1e-7,
                    atol=1e-9,
                )
            )
        )
        if protection_changed:
            self._last_shared_protection = np.array(protection, copy=True)

        if self._satellites:
            active_satellites: tuple[int | None, ...] = tuple(self._satellites)
        else:
            supplied = tuple(source_satellites or ())
            active_satellites = tuple(
                (
                    int(supplied[index])
                    if index < len(supplied) and supplied[index] is not None
                    else None
                )
                for index in range(self._source_count)
            )
        rows = np.empty(
            (self._source_count, self._channel_count),
            dtype=np.complex128,
        )
        status: dict[str, object] = {}
        for row_index, prn in enumerate(active_satellites):
            if prn is None:
                acquisition_row = (
                    protection
                    if enabled and shared_measured_u1_available
                    else common
                )
                acquisition_source = (
                    "shared_measured_u1_acquisition_waiting_for_channel_prn"
                    if enabled and shared_measured_u1_available
                    else "shared_common_waiting_for_channel_prn"
                )
                rows[row_index] = acquisition_row
                if emit_status:
                    status[f"source_{row_index:02d}"] = {
                        "source": acquisition_source,
                        "source_index": row_index,
                        "satellite": None,
                        "applied_to_gnss_sdr": True,
                        "shared_spatial_solution": True,
                        "independent_per_prn_lcmv": False,
                        "desired_vector_available": False,
                        "phase_compensation_applied": False,
                        "shared_protection_bridge_applied": bool(
                            enabled and shared_measured_u1_available
                        ),
                        "phase_compensation_guard_reason": (
                            "tracking channel has no current GPS PRN assignment"
                        ),
                        "transition_active": False,
                        "transition_progress": 1.0,
                        "applied_logical_weights": _complex_payload(acquisition_row),
                        "desired_spatial_vector": None,
                    }
                continue
            satellite = f"G{prn:02d}"
            state = self._states.get(row_index)
            if state is None or state.satellite != satellite:
                initial = (
                    protection
                    if enabled and shared_measured_u1_available
                    else common
                )
                state = _PhaseState(
                    satellite=satellite,
                    current=np.array(initial, copy=True),
                    start=np.array(initial, copy=True),
                    target=np.array(initial, copy=True),
                    source=(
                        "shared_measured_u1_acquisition_new_prn"
                        if enabled and shared_measured_u1_available
                        else "shared_common_weights"
                    ),
                )
                self._states[row_index] = state
            payload = desired_vectors.get(satellite, {})
            vector = self._valid_vector(
                payload.get("desired_spatial_vector"), self._channel_count
            )
            updated = _finite_float(payload.get("updated_monotonic"))
            age = now - updated if updated is not None else math.inf
            fresh = bool(0.0 <= age <= max(0.1, float(max_vector_age_s)))
            counter = payload.get("tracking_sample_counter")
            try:
                quality_counter = int(counter) if counter is not None else None
            except (TypeError, ValueError):
                quality_counter = None

            post_onset_adopted = False
            desired_step_deg: float | None = None
            if not enabled and vector is not None and fresh:
                state.desired = self._align_to_reference(vector, state.desired)
                state.post_onset_vector = None
                state.post_onset_pass_count = 0
                state.last_quality_counter = quality_counter
            elif (
                enabled
                and state.desired is None
                and vector is not None
                and fresh
                and updated is not None
                and self._activation_monotonic is not None
                and updated > self._activation_monotonic
                and quality_counter is not None
                and quality_counter != state.last_quality_counter
            ):
                state.last_quality_counter = quality_counter
                aligned = self._align_to_reference(vector, state.post_onset_vector)
                if state.post_onset_vector is None:
                    state.post_onset_pass_count = 1
                else:
                    coherence = phase_invariant_coherence(
                        state.post_onset_vector, aligned
                    )
                    desired_step_deg = float(
                        np.degrees(np.arccos(np.sqrt(np.clip(coherence, 0.0, 1.0))))
                    )
                    state.post_onset_pass_count = (
                        state.post_onset_pass_count + 1
                        if desired_step_deg <= self._max_post_onset_step_deg
                        else 1
                    )
                state.post_onset_vector = aligned
                if state.post_onset_pass_count >= self._min_post_onset_updates:
                    state.desired = self._align_to_reference(aligned, None)
                    post_onset_adopted = True

            protection_needs_update = bool(
                shared_measured_u1_available
                and state.desired is not None
                and (
                    not state.source.startswith(
                        "phase_compensated_shared_measured_u1"
                    )
                    or protection_changed
                )
            )
            if enabled and state.desired is None and shared_measured_u1_available:
                # Acquisition has no established carrier phase to preserve.
                # Apply the shared protected row directly so new satellites can
                # still acquire while the jammer is present.
                state.current = np.array(protection, copy=True)
                state.start = np.array(protection, copy=True)
                state.target = np.array(protection, copy=True)
                state.source = "shared_measured_u1_acquisition_new_prn"
                state.total_chunks = 0
                state.completed_chunks = 0
                state.scale = 1.0 + 0.0j
                state.requested_amplitude_compensation_db = None
                state.continuity_residual_abs = None
                state.guard_reason = None
            elif rising and protection_needs_update:
                self._start_transition(
                    state,
                    requested=protection,
                    source="phase_compensated_shared_measured_u1",
                    immediate=True,
                )
            elif (
                enabled
                and protection_needs_update
            ):
                # The jammer latch and covariance worker are concurrent. If
                # the latch reaches this thread one chunk before measured-U1
                # weights are published, apply them on the first later chunk
                # instead of freezing the temporary uniform hold.
                self._start_transition(
                    state,
                    requested=protection,
                    source="phase_compensated_shared_measured_u1",
                    immediate=True,
                )
            elif falling:
                self._start_transition(
                    state,
                    requested=common,
                    source="phase_compensated_shared_uniform_after_jammer",
                    immediate=False,
                )
            elif post_onset_adopted:
                self._start_transition(
                    state,
                    requested=protection,
                    source="phase_compensated_shared_measured_u1_new_prn",
                    immediate=True,
                )
            elif not enabled and state.desired is None:
                # Before a PRN has a usable vector, keep every source identical.
                state.current = np.array(common, copy=True)
                state.start = np.array(common, copy=True)
                state.target = np.array(common, copy=True)
                state.source = "shared_common_waiting_for_prn_vector"
                state.total_chunks = 0
                state.completed_chunks = 0

            self._advance_transition(state)
            rows[row_index] = state.current
            if not emit_status:
                continue
            response = (
                complex(np.vdot(state.current, state.desired))
                if state.desired is not None
                else None
            )
            transition_active = bool(
                state.total_chunks > 0
                and state.completed_chunks < state.total_chunks
            )
            status_key = (
                satellite
                if satellite not in status
                else f"{satellite}@source_{row_index:02d}"
            )
            status[status_key] = {
                "source": state.source,
                "source_index": row_index,
                "satellite": satellite,
                "applied_to_gnss_sdr": True,
                "shared_spatial_solution": True,
                "independent_per_prn_lcmv": False,
                "shared_measured_u1_available": bool(
                    shared_measured_u1_available
                ),
                "desired_vector_available": state.desired is not None,
                "desired_vector_frozen": enabled and state.desired is not None,
                "desired_vector_age_s": age if math.isfinite(age) else None,
                "post_onset_desired_adopted": post_onset_adopted,
                "post_onset_quality_pass_count": state.post_onset_pass_count,
                "post_onset_desired_step_deg": desired_step_deg,
                "phase_compensation_applied": state.source.startswith(
                    "phase_compensated_"
                ),
                "shared_protection_bridge_applied": state.source.startswith(
                    "phase_compensated_shared_measured_u1"
                ),
                "phase_compensation_guard_reason": state.guard_reason,
                "weight_phase_correction_deg": float(np.degrees(np.angle(state.scale))),
                "output_phase_compensation_deg": float(
                    np.degrees(np.angle(np.conj(state.scale)))
                ),
                "amplitude_compensation_db": (
                    20.0 * math.log10(abs(state.scale))
                    if abs(state.scale) > 0.0
                    else None
                ),
                "requested_exact_amplitude_compensation_db": (
                    state.requested_amplitude_compensation_db
                ),
                "continuity_residual_abs": state.continuity_residual_abs,
                "applied_response": (
                    {"real": float(response.real), "imag": float(response.imag)}
                    if response is not None
                    else None
                ),
                "transition_active": transition_active,
                "transition_progress": (
                    state.completed_chunks / float(state.total_chunks)
                    if transition_active
                    else 1.0
                ),
                "applied_logical_weights": _complex_payload(state.current),
                "desired_spatial_vector": (
                    _complex_payload(state.desired)
                    if state.desired is not None
                    else None
                ),
            }
        return rows, status


class PerPrnMeasuredVectorBeamformerBank:
    """Build one continuously updated spatial row for every mapped GPS PRN.

    With no jammer, each row is the minimum-norm measured-vector combiner that
    preserves the response already seen by that tracking channel.  With a
    jammer, each row is an independent covariance LCMV solution constrained by
    that PRN's measured vector and the measured jammer vector.  Every target is
    then scaled so ``w_new**H a_prn == w_old**H a_prn``.  The scale therefore
    cannot move or fill the LCMV null, and no arbitrary dB gain cap is used.
    """

    def __init__(
        self,
        *,
        satellites: tuple[int, ...],
        channel_count: int,
        sample_rate_hz: float,
        samples_per_chunk: int,
        transition_s: float,
        source_count: int | None = None,
        max_weight_norm: float = 8.0,
    ) -> None:
        self._satellites = tuple(int(value) for value in satellites)
        self._source_count = (
            len(self._satellites)
            if self._satellites
            else max(1, int(source_count or 0))
        )
        self._channel_count = int(channel_count)
        chunk_s = max(1, int(samples_per_chunk)) / max(1.0, float(sample_rate_hz))
        self._transition_chunks = (
            0
            if float(transition_s) <= 0.0
            else max(1, int(math.ceil(float(transition_s) / chunk_s)))
        )
        self._max_weight_norm = max(float(max_weight_norm), 1e-6)
        self._states: dict[int, _PerPrnState] = {}
        self._source_assignments: dict[int, str | None] = {}
        self._vector_not_before_monotonic: dict[int, float] = {}
        self._jammer_previous = False
        self._last_shared_protection: np.ndarray | None = None
        self._last_lcmv_covariance: np.ndarray | None = None
        self._last_lcmv_jammer_vector: np.ndarray | None = None
        self._lcmv_context_generation = 0

    @staticmethod
    def _valid_vector(value: object, size: int) -> np.ndarray | None:
        vector = np.asarray(
            value if value is not None else [], dtype=np.complex128
        ).reshape(-1)
        if (
            vector.size != int(size)
            or not np.all(np.isfinite(vector))
            or float(np.linalg.norm(vector)) <= np.finfo(np.float64).tiny
        ):
            return None
        return vector / float(np.linalg.norm(vector))

    @staticmethod
    def _align_to_reference(
        desired: np.ndarray,
        reference: np.ndarray | None,
    ) -> np.ndarray:
        return SharedU1PhaseCompensationBank._align_to_reference(
            desired, reference
        )

    @staticmethod
    def _advance_transition(state: _PerPrnState) -> None:
        if state.total_chunks <= 0 or state.completed_chunks >= state.total_chunks:
            return
        state.completed_chunks += 1
        alpha = state.completed_chunks / float(state.total_chunks)
        state.current = np.asarray(
            (1.0 - alpha) * state.start + alpha * state.target,
            dtype=np.complex128,
        )

    def _set_target(
        self,
        state: _PerPrnState,
        target: np.ndarray,
        *,
        source: str,
        immediate: bool,
    ) -> bool:
        requested = np.asarray(target, dtype=np.complex128).reshape(-1)
        if (
            requested.size != self._channel_count
            or not np.all(np.isfinite(requested))
        ):
            state.guard_reason = "per-PRN target is invalid"
            return False
        norm = float(np.linalg.norm(requested))
        if not np.isfinite(norm) or norm > self._max_weight_norm:
            state.guard_reason = (
                "per-PRN target norm exceeded: "
                f"{norm:.6g} > {self._max_weight_norm:.6g}"
            )
            return False
        state.start = np.array(state.current, copy=True)
        state.target = np.array(requested, copy=True)
        state.source = str(source)
        state.total_chunks = 1 if immediate else self._transition_chunks
        state.completed_chunks = 0
        state.guard_reason = None
        if state.total_chunks == 0:
            state.current = np.array(state.target, copy=True)
        return True

    def _continuous_scale(
        self,
        *,
        state: _PerPrnState,
        requested: np.ndarray,
        desired: np.ndarray,
    ) -> np.ndarray:
        preserved = complex(np.vdot(state.current, desired))
        raw = complex(np.vdot(requested, desired))
        floor = 1e-10 * max(
            1.0, float(np.linalg.norm(requested) * np.linalg.norm(desired))
        )
        if abs(raw) <= floor:
            raise ValueError("per-PRN requested response is near zero")
        scale = complex(np.conj(preserved / raw))
        target = np.asarray(scale * requested, dtype=np.complex128)
        state.continuity_residual_abs = float(
            abs(np.vdot(target, desired) - preserved)
        )
        return target

    def _matched_target(
        self,
        state: _PerPrnState,
        desired: np.ndarray,
    ) -> np.ndarray:
        """Minimum-norm row with exactly the current PRN response."""

        response = complex(np.vdot(state.current, desired))
        power = float(np.vdot(desired, desired).real)
        if power <= np.finfo(np.float64).tiny:
            raise ValueError("per-PRN desired vector has zero power")
        target = np.asarray(desired * np.conj(response) / power, dtype=np.complex128)
        state.continuity_residual_abs = float(
            abs(np.vdot(target, desired) - response)
        )
        state.lcmv_null_residual_abs = None
        state.lcmv_condition_number = None
        state.lcmv_weight_norm = float(np.linalg.norm(target))
        return target

    def _lcmv_target(
        self,
        state: _PerPrnState,
        *,
        desired: np.ndarray,
        covariance: np.ndarray,
        jammer_vector: np.ndarray,
        diagonal_loading_rel: float,
        diagonal_loading_abs: float,
        condition_number_limit: float,
    ) -> np.ndarray:
        result = covariance_lcmv_vector_null_weights(
            covariance=covariance,
            null_vector=jammer_vector,
            preserve_vector=desired,
            diagonal_loading_rel=diagonal_loading_rel,
            diagonal_loading_abs=diagonal_loading_abs,
            condition_number_limit=condition_number_limit,
            max_weight_norm=self._max_weight_norm,
        )
        target = self._continuous_scale(
            state=state,
            requested=np.asarray(result.weights, dtype=np.complex128),
            desired=desired,
        )
        state.lcmv_null_residual_abs = float(
            abs(np.vdot(target, result.null_vector))
        )
        state.lcmv_condition_number = float(result.condition_number)
        state.lcmv_weight_norm = float(np.linalg.norm(target))
        return target

    def _phase_continuous_shared_fallback(
        self,
        state: _PerPrnState,
        *,
        desired: np.ndarray,
        shared: np.ndarray,
    ) -> np.ndarray:
        """Bridge covariance publication while preserving complex response."""

        preserved = complex(np.vdot(state.current, desired))
        raw = complex(np.vdot(shared, desired))
        if abs(raw) <= 1e-10:
            raise ValueError("shared fallback has near-zero PRN response")
        scale = complex(np.conj(preserved / raw))
        target = np.asarray(scale * shared, dtype=np.complex128)
        state.continuity_residual_abs = float(
            abs(np.vdot(target, desired) - preserved)
        )
        state.lcmv_null_residual_abs = None
        state.lcmv_condition_number = None
        state.lcmv_weight_norm = float(np.linalg.norm(target))
        return target

    def advance(
        self,
        *,
        shared_common_weights: np.ndarray,
        shared_measured_u1_weights: np.ndarray,
        shared_measured_u1_available: bool,
        desired_vectors: dict[str, dict[str, object]],
        source_satellites: tuple[int | None, ...] | None = None,
        enabled_now: bool,
        covariance: np.ndarray | None = None,
        jammer_vector: np.ndarray | None = None,
        diagonal_loading_rel: float = 1e-3,
        diagonal_loading_abs: float = 0.0,
        condition_number_limit: float = 1e8,
        now_monotonic: float | None = None,
        max_vector_age_s: float = 2.5,
        emit_status: bool = True,
    ) -> tuple[np.ndarray, dict[str, object]]:
        common = np.asarray(shared_common_weights, dtype=np.complex128).reshape(-1)
        shared = np.asarray(
            shared_measured_u1_weights, dtype=np.complex128
        ).reshape(-1)
        if common.size != self._channel_count or shared.size != self._channel_count:
            raise ValueError("per-PRN beamformer weight length mismatch")
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        jammer_active = bool(enabled_now)
        jammer_rising = jammer_active and not self._jammer_previous
        jammer_falling = not jammer_active and self._jammer_previous
        self._jammer_previous = jammer_active
        shared_context_changed = bool(
            shared_measured_u1_available
            and (
                self._last_shared_protection is None
                or not np.allclose(
                    self._last_shared_protection, shared, rtol=1e-7, atol=1e-9
                )
            )
        )
        if shared_context_changed:
            self._last_shared_protection = np.array(shared, copy=True)
        cov = np.asarray(
            covariance if covariance is not None else [], dtype=np.complex128
        )
        null = self._valid_vector(jammer_vector, self._channel_count)
        lcmv_context_available = bool(
            shared_measured_u1_available
            and cov.shape == (self._channel_count, self._channel_count)
            and np.all(np.isfinite(cov))
            and null is not None
        )
        lcmv_context_changed = bool(
            lcmv_context_available
            and (
                self._last_lcmv_covariance is None
                or self._last_lcmv_jammer_vector is None
                or not np.allclose(
                    self._last_lcmv_covariance,
                    cov,
                    rtol=1e-7,
                    atol=1e-9,
                )
                or not np.allclose(
                    self._last_lcmv_jammer_vector,
                    null,
                    rtol=1e-7,
                    atol=1e-9,
                )
            )
        )
        if lcmv_context_changed:
            self._lcmv_context_generation += 1
            self._last_lcmv_covariance = np.array(cov, copy=True)
            self._last_lcmv_jammer_vector = np.array(null, copy=True)
        elif not lcmv_context_available:
            # Do not let a later reappearance look identical to a context that
            # was unavailable in between. Every row must reconsider it.
            self._last_lcmv_covariance = None
            self._last_lcmv_jammer_vector = None

        if self._satellites:
            active_satellites: tuple[int | None, ...] = tuple(self._satellites)
        else:
            supplied = tuple(source_satellites or ())
            active_satellites = tuple(
                (
                    int(supplied[index])
                    if index < len(supplied) and supplied[index] is not None
                    else None
                )
                for index in range(self._source_count)
            )
        rows = np.empty(
            (self._source_count, self._channel_count), dtype=np.complex128
        )
        status: dict[str, object] = {}
        for row_index, prn in enumerate(active_satellites):
            satellite = f"G{int(prn):02d}" if prn is not None else None
            assignment_known = row_index in self._source_assignments
            previous_assignment = self._source_assignments.get(row_index)
            assignment_changed = bool(
                assignment_known and previous_assignment != satellite
            )
            if assignment_changed:
                # A channel/FIFO slot has started a new assignment epoch. A
                # vector published before this boundary belongs to the old
                # tracking epoch even when the same PRN later returns.
                self._vector_not_before_monotonic[row_index] = now
            self._source_assignments[row_index] = satellite
            if prn is None:
                # A gap is a real end of ownership, not a pause. Keeping this
                # row's state would freeze old carrier response and LCMV
                # context into a later same-PRN reacquisition.
                self._states.pop(row_index, None)
                row = shared if jammer_active and shared_measured_u1_available else common
                rows[row_index] = row
                if emit_status:
                    status[f"source_{row_index:02d}"] = {
                        "source": (
                            "shared_protected_acquisition_waiting_for_prn"
                            if jammer_active and shared_measured_u1_available
                            else "uniform_acquisition_waiting_for_prn"
                        ),
                        "source_index": row_index,
                        "satellite": None,
                        "applied_to_gnss_sdr": True,
                        "shared_spatial_solution": True,
                        "independent_per_prn_lcmv": False,
                        "independent_per_prn_beamforming": False,
                        "desired_vector_available": False,
                        "transition_active": False,
                        "applied_logical_weights": _complex_payload(row),
                        "desired_spatial_vector": None,
                    }
                continue

            assert satellite is not None
            state = self._states.get(row_index)
            reassigned = state is None or state.satellite != satellite
            if reassigned:
                initial = (
                    shared
                    if jammer_active and shared_measured_u1_available
                    else common
                )
                state = _PerPrnState(
                    satellite=satellite,
                    current=np.array(initial, copy=True),
                    start=np.array(initial, copy=True),
                    target=np.array(initial, copy=True),
                    source=(
                        "shared_protected_new_prn_waiting_for_vector"
                        if jammer_active and shared_measured_u1_available
                        else "uniform_new_prn_waiting_for_vector"
                    ),
                )
                self._states[row_index] = state

            payload = desired_vectors.get(satellite, {})
            vector = self._valid_vector(
                payload.get("desired_spatial_vector"), self._channel_count
            )
            updated = _finite_float(payload.get("updated_monotonic"))
            age = now - updated if updated is not None else math.inf
            epoch_floor = self._vector_not_before_monotonic.get(row_index)
            from_current_assignment = bool(
                updated is not None
                and (epoch_floor is None or updated > epoch_floor)
            )
            fresh = bool(
                0.0 <= age <= max(0.1, float(max_vector_age_s))
                and from_current_assignment
            )
            try:
                quality_counter = int(payload.get("tracking_sample_counter"))
            except (TypeError, ValueError):
                quality_counter = None
            vector_changed = bool(
                vector is not None
                and fresh
                and quality_counter is not None
                and quality_counter != state.last_quality_counter
            )
            # Keep every established pre-jammer desired vector fixed for the
            # full protection interval.  Otherwise jammer-contaminated or
            # low-C/N0 measurements can redefine what the LCMV row preserves.
            # A source acquired during jamming may still adopt its first valid
            # vector; it is frozen immediately after that first adoption.
            adopt_vector = bool(
                vector_changed and (not jammer_active or state.desired is None)
            )
            if adopt_vector:
                aligned = self._align_to_reference(vector, state.desired)
                state.desired_step_deg = (
                    None
                    if state.desired is None
                    else float(
                        np.degrees(
                            np.arccos(
                                np.sqrt(
                                    np.clip(
                                        phase_invariant_coherence(
                                            state.desired, aligned
                                        ),
                                        0.0,
                                        1.0,
                                    )
                                )
                            )
                        )
                    )
                )
                state.desired = aligned
                state.last_quality_counter = quality_counter
                state.desired_updated_monotonic = updated

            transition_in_progress = bool(
                state.total_chunks > 0
                and state.completed_chunks < state.total_chunks
            )
            pending_lcmv_context = bool(
                jammer_active
                and lcmv_context_available
                and state.last_lcmv_context_generation
                != self._lcmv_context_generation
            )

            needs_target = bool(
                state.desired is not None
                and (
                    adopt_vector
                    or jammer_rising
                    or jammer_falling
                    or (
                        pending_lcmv_context
                        and not transition_in_progress
                    )
                    or (
                        jammer_active
                        and not lcmv_context_available
                        and shared_context_changed
                        and not transition_in_progress
                    )
                    or not state.source.startswith("per_prn_")
                )
            )
            if needs_target:
                try:
                    if jammer_active and lcmv_context_available:
                        target = self._lcmv_target(
                            state,
                            desired=state.desired,
                            covariance=cov,
                            jammer_vector=null,
                            diagonal_loading_rel=diagonal_loading_rel,
                            diagonal_loading_abs=diagonal_loading_abs,
                            condition_number_limit=condition_number_limit,
                        )
                        source_label = "per_prn_measured_vector_lcmv"
                    elif jammer_active and shared_measured_u1_available:
                        target = self._phase_continuous_shared_fallback(
                            state,
                            desired=state.desired,
                            shared=shared,
                        )
                        source_label = "per_prn_phase_continuous_shared_onset_bridge"
                    else:
                        target = self._matched_target(state, state.desired)
                        source_label = "per_prn_measured_vector_matched"
                    accepted = self._set_target(
                        state,
                        target,
                        source=source_label,
                        immediate=reassigned,
                    )
                    if accepted:
                        state.last_lcmv_context_generation = (
                            self._lcmv_context_generation
                            if source_label == "per_prn_measured_vector_lcmv"
                            else None
                        )
                except (ValueError, np.linalg.LinAlgError) as exc:
                    state.guard_reason = str(exc)

            self._advance_transition(state)
            rows[row_index] = state.current
            if not emit_status:
                continue
            response = (
                complex(np.vdot(state.current, state.desired))
                if state.desired is not None
                else None
            )
            desired_age = (
                now - state.desired_updated_monotonic
                if state.desired_updated_monotonic is not None
                else math.inf
            )
            transition_active = bool(
                state.total_chunks > 0
                and state.completed_chunks < state.total_chunks
            )
            context_update_pending = bool(
                jammer_active
                and lcmv_context_available
                and state.last_lcmv_context_generation
                != self._lcmv_context_generation
            )
            status_key = (
                satellite
                if satellite not in status
                else f"{satellite}@source_{row_index:02d}"
            )
            status[status_key] = {
                "source": state.source,
                "source_index": row_index,
                "satellite": satellite,
                "applied_to_gnss_sdr": True,
                "shared_spatial_solution": not state.source.startswith("per_prn_"),
                "independent_per_prn_lcmv": state.source == (
                    "per_prn_measured_vector_lcmv"
                ),
                "independent_per_prn_beamforming": state.source.startswith(
                    "per_prn_"
                ),
                "desired_vector_available": state.desired is not None,
                "desired_vector_frozen": bool(
                    jammer_active and state.desired is not None
                ),
                "desired_vector_age_s": (
                    desired_age if math.isfinite(desired_age) else None
                ),
                "desired_vector_step_deg": state.desired_step_deg,
                "tracking_sample_counter": state.last_quality_counter,
                "jammer_context_available": lcmv_context_available,
                "lcmv_context_generation": (
                    self._lcmv_context_generation
                    if lcmv_context_available
                    else None
                ),
                "applied_lcmv_context_generation": (
                    state.last_lcmv_context_generation
                ),
                "lcmv_context_update_pending": context_update_pending,
                "phase_compensation_applied": state.desired is not None,
                "phase_compensation_guard_reason": state.guard_reason,
                "continuity_residual_abs": state.continuity_residual_abs,
                "lcmv_null_residual_abs": state.lcmv_null_residual_abs,
                "lcmv_condition_number": state.lcmv_condition_number,
                "lcmv_weight_norm": state.lcmv_weight_norm,
                "applied_response": (
                    {"real": float(response.real), "imag": float(response.imag)}
                    if response is not None
                    else None
                ),
                "transition_active": transition_active,
                "transition_progress": (
                    state.completed_chunks / float(state.total_chunks)
                    if transition_active
                    else 1.0
                ),
                "applied_logical_weights": _complex_payload(state.current),
                "desired_spatial_vector": (
                    _complex_payload(state.desired)
                    if state.desired is not None
                    else None
                ),
            }
        return rows, status


class SharedU1DesiredVectorMonitor:
    """Measure current PRN spatial vectors from a rolling quality window."""

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        channel_count: int,
        phase_correction_vector: tuple[complex, ...] | None,
        tracking_snapshot: Callable[[], dict[str, object]],
        session_dir: Path,
        session_id: str,
        logger: logging.Logger,
        satellites: tuple[int, ...],
        source_count: int | None = None,
        frontend_group_delay_samples: int = 0,
        retention_s: float = 0.75,
        measurement_interval_s: float = 1.0,
        min_cno_db_hz: float = 30.0,
        min_quality_measurements: int = 3,
        vector_window_measurements: int = 12,
    ) -> None:
        self._fs = float(sample_rate_hz)
        self._frontend_group_delay_samples = int(frontend_group_delay_samples)
        if self._frontend_group_delay_samples < 0:
            raise ValueError("frontend_group_delay_samples must be non-negative")
        self._channel_count = int(channel_count)
        correction = phase_correction_vector or tuple(
            1.0 + 0.0j for _ in range(self._channel_count)
        )
        self._correction = np.asarray(correction, dtype=np.complex64).reshape(-1)
        if self._correction.size != self._channel_count:
            raise ValueError("phase correction length does not match channel count")
        self._tracking_snapshot = tracking_snapshot
        self._session_id = str(session_id)
        self._logger = logger
        self._retention_samples = max(
            int(round(max(0.1, float(retention_s)) * self._fs)),
            int(round(0.02 * self._fs)),
        )
        self._measurement_interval_s = max(0.2, float(measurement_interval_s))
        self._min_cno_db_hz = float(min_cno_db_hz)
        self._min_quality_measurements = max(1, int(min_quality_measurements))
        self._vector_window_measurements = max(
            self._min_quality_measurements,
            int(vector_window_measurements),
        )
        self._satellite_rows = {
            f"G{int(prn):02d}": index for index, prn in enumerate(satellites)
        }
        self._dynamic_sources = not bool(self._satellite_rows)
        self._source_count = (
            len(self._satellite_rows)
            if self._satellite_rows
            else max(1, int(source_count or 0))
        )
        self._path = Path(session_dir) / "shared_u1_phase_vectors.jsonl"
        self._queue: queue.Queue[
            tuple[int, np.ndarray, np.ndarray] | None
        ] = queue.Queue(maxsize=256)
        self._thread: threading.Thread | None = None
        self._spans: deque[_RawSpan] = deque()
        self._latest_end = 0
        self._last_counter: dict[str, int] = {}
        self._last_measurement_monotonic = 0.0
        self._code_cache: dict[int, np.ndarray] = {}
        self._aggregates: dict[str, _PrnAggregate] = {}
        self._vectors_lock = threading.Lock()
        self._latest_vectors: dict[str, dict[str, object]] = {}
        self._handle = None
        self._queue_drops = 0

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

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8", buffering=1)
        self._thread = threading.Thread(
            target=self._run,
            name="gnss_shared_u1_phase_vectors",
            daemon=True,
        )
        self._thread.start()
        self._write(
            {
                "event": "shared_u1_phase_vector_monitor_start",
                "schema_version": 1,
                "satellites": sorted(self._satellite_rows),
                "dynamic_channel_prn_mapping": self._dynamic_sources,
                "source_count": self._source_count,
                "purpose": (
                    "rolling PRN code-despread desired vectors for independent "
                    "per-PRN measured-vector beamforming and LCMV"
                ),
                "vector_window_measurements": self._vector_window_measurements,
                "tracking_counter_domain": "post_input_filter",
                "raw_iq_domain": "pre_input_filter",
                "frontend_group_delay_samples": self._frontend_group_delay_samples,
                "tracking_to_raw_counter_offset_samples": (
                    -self._frontend_group_delay_samples
                ),
            }
        )

    def submit(
        self,
        sample_start: int,
        raw_chunk: np.ndarray,
        logical_weights: np.ndarray,
    ) -> None:
        item = (
            int(sample_start),
            np.asarray(raw_chunk, dtype=np.complex64),
            np.asarray(logical_weights, dtype=np.complex128).copy(),
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._queue_drops += 1

    def stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._write(
            {
                "event": "shared_u1_phase_vector_monitor_stop",
                "queue_drops": self._queue_drops,
            }
        )
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.close()

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                item = ()
            if item is None:
                return
            if item:
                self._append_span(*item)
            now = time.monotonic()
            if (
                self._latest_end > 0
                and now - self._last_measurement_monotonic
                >= self._measurement_interval_s
            ):
                self._last_measurement_monotonic = now
                try:
                    self._measure_tracking()
                except Exception as exc:
                    self._logger.warning(
                        "Shared-U1 PRN vector measurement failed: %s", exc
                    )

    def _append_span(
        self,
        start: int,
        raw: np.ndarray,
        logical_weights: np.ndarray,
    ) -> None:
        if raw.ndim != 2 or raw.shape != (self._channel_count, raw.shape[1]):
            return
        if raw.shape[1] == 0:
            return
        end = int(start) + int(raw.shape[1])
        self._spans.append(_RawSpan(int(start), end, raw, logical_weights))
        self._latest_end = max(self._latest_end, end)
        keep_from = self._latest_end - self._retention_samples
        while self._spans and self._spans[0].end <= keep_from:
            self._spans.popleft()

    def _extract(
        self,
        start: int,
        end: int,
        row_index: int,
    ) -> tuple[np.ndarray, np.ndarray, bool] | None:
        if end <= start or not self._spans:
            return None
        output = np.empty((self._channel_count, end - start), dtype=np.complex64)
        filled = 0
        rows: list[np.ndarray] = []
        for span in self._spans:
            overlap_start = max(start, span.start)
            overlap_end = min(end, span.end)
            if overlap_end <= overlap_start:
                continue
            source_start = overlap_start - span.start
            target_start = overlap_start - start
            count = overlap_end - overlap_start
            output[:, target_start : target_start + count] = span.raw[
                :, source_start : source_start + count
            ]
            filled += count
            logical = np.asarray(span.logical_weights, dtype=np.complex128)
            if logical.ndim == 1:
                selected = logical
            elif logical.ndim == 2 and 0 <= row_index < logical.shape[0]:
                selected = logical[row_index]
            else:
                return None
            if selected.size != self._channel_count:
                return None
            rows.append(np.asarray(selected, dtype=np.complex128))
        if filled != end - start or not rows:
            return None
        changed = any(
            not np.allclose(value, rows[0], rtol=1e-6, atol=1e-8)
            for value in rows[1:]
        )
        return output, rows[0], changed

    def _measure_tracking(self) -> None:
        entries = self._tracking_snapshot().get("tracking_monitor", [])
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            system = str(entry.get("system", "G")).upper()
            signal = str(entry.get("signal", ""))
            prn = int(entry.get("prn", 0) or 0)
            satellite = str(entry.get("satellite_id") or f"G{prn:02d}")
            try:
                tracking_channel = int(entry.get("channel", -1))
            except (TypeError, ValueError):
                tracking_channel = -1
            source_index = (
                tracking_channel
                if self._dynamic_sources
                else self._satellite_rows.get(satellite, -1)
            )
            counter = int(entry.get("tracking_sample_counter", 0) or 0)
            cno = _finite_float(entry.get("cn0_db_hz"))
            doppler = _finite_float(entry.get("carrier_doppler_hz"))
            if (
                (not self._dynamic_sources and satellite not in self._satellite_rows)
                or source_index < 0
                or source_index >= self._source_count
                or system not in {"G", "GPS"}
                or signal != "1C"
                or counter <= 0
                or cno is None
                or cno < self._min_cno_db_hz
                or doppler is None
                or self._last_counter.get(satellite) == counter
            ):
                continue
            measurement = self._correlate(
                entry, satellite, prn, counter, doppler, cno, source_index
            )
            if measurement is not None:
                self._last_counter[satellite] = counter
                self._write(measurement)

    def _correlate(
        self,
        entry: dict[str, object],
        satellite: str,
        prn: int,
        counter: int,
        doppler_hz: float,
        cno_db_hz: float,
        source_index: int,
    ) -> dict[str, object] | None:
        raw_counter = counter - self._frontend_group_delay_samples
        code_rate_hz = GPS_CA_RATE_HZ * (1.0 + doppler_hz / GPS_L1_HZ)
        nominal_length = self._fs * GPS_CA_LENGTH / code_rate_hz
        lengths = sorted(
            {
                max(1, int(math.floor(nominal_length)) + delta)
                for delta in (-1, 0, 1, 2)
            }
        )
        margin = 3
        extracted = self._extract(
            raw_counter - max(lengths) - margin,
            raw_counter + margin,
            source_index,
        )
        if extracted is None:
            return None
        raw, logical_weights, weight_changed = extracted
        calibrated = raw * self._correction[:, None]
        combined = np.sum(np.conj(logical_weights)[:, None] * calibrated, axis=0)
        code = self._code_cache.setdefault(prn, gps_l1_ca_code(prn))
        best: tuple[float, int, int, float, np.ndarray] | None = None
        for length in lengths:
            n = np.arange(length, dtype=np.float64)
            carrier = np.exp(-1j * 2.0 * np.pi * doppler_hz / self._fs * n)
            for end_delta in (-1, 0, 1):
                end_index = raw.shape[1] - margin + end_delta
                start_index = end_index - length
                if start_index < 0 or end_index > raw.shape[1]:
                    continue
                segment = combined[start_index:end_index]
                for rem_samples in np.linspace(-0.5, 1.5, 9):
                    chips = np.floor(
                        (n + rem_samples) * code_rate_hz / self._fs
                    ).astype(np.int64) % GPS_CA_LENGTH
                    replica = code[chips] * carrier
                    score = float(abs(np.sum(segment * replica, dtype=np.complex128)))
                    if best is None or score > best[0]:
                        best = (score, start_index, end_index, rem_samples, replica)
        if best is None:
            return None
        score, start_index, end_index, rem_samples, replica = best
        prompts = np.sum(
            calibrated[:, start_index:end_index] * replica[None, :],
            axis=1,
            dtype=np.complex128,
        )
        norm = float(np.linalg.norm(prompts))
        if norm <= np.finfo(np.float64).tiny:
            return None
        instantaneous = prompts / norm
        length = end_index - start_index
        n = np.arange(length, dtype=np.float64)
        chips = np.floor(
            (n + rem_samples) * code_rate_hz / self._fs
        ).astype(np.int64) % GPS_CA_LENGTH
        off_replica = np.roll(code, GPS_CA_LENGTH // 4)[chips] * np.exp(
            -1j * 2.0 * np.pi * doppler_hz / self._fs * n
        )
        off_prompt = np.sum(
            combined[start_index:end_index] * off_replica,
            dtype=np.complex128,
        )
        prompt_vs_off_db = 20.0 * math.log10(
            max(score, np.finfo(np.float64).tiny)
            / max(abs(off_prompt), np.finfo(np.float64).tiny)
        )
        quality_pass = bool(prompt_vs_off_db >= 6.0 and not weight_changed)
        aggregate = self._aggregates.get(satellite)
        if quality_pass:
            projector = np.outer(instantaneous, np.conj(instantaneous))
            if aggregate is None or aggregate.source_index != source_index:
                aggregate = _PrnAggregate(
                    total_count=0,
                    projectors=deque(maxlen=self._vector_window_measurements),
                    source_index=source_index,
                )
                self._aggregates[satellite] = aggregate
            aggregate.total_count += 1
            aggregate.projectors.append(
                np.asarray(projector, dtype=np.complex128)
            )
        if aggregate is None:
            aggregate_vector = _align_common_phase(instantaneous)
            aggregate_count = 0
            rolling_count = 0
            concentration = None
        else:
            rolling_count = len(aggregate.projectors)
            projector_mean = np.mean(
                np.stack(tuple(aggregate.projectors), axis=0), axis=0
            )
            eigenvalues, eigenvectors = np.linalg.eigh(
                projector_mean
            )
            aggregate_vector = _align_common_phase(
                eigenvectors[:, int(np.argmax(eigenvalues))]
            )
            aggregate_count = aggregate.total_count
            concentration = float(
                np.max(eigenvalues)
                / max(float(np.sum(eigenvalues)), np.finfo(np.float64).tiny)
            )
        if quality_pass and aggregate_count >= self._min_quality_measurements:
            with self._vectors_lock:
                self._latest_vectors[satellite] = {
                    "desired_spatial_vector": np.asarray(
                        aggregate_vector, dtype=np.complex128
                    ),
                    "quality_pass_count": aggregate_count,
                    "rolling_quality_measurement_count": rolling_count,
                    "tracking_sample_counter": int(counter),
                    "raw_sample_counter": int(raw_counter),
                    "updated_monotonic": float(time.monotonic()),
                }
        return {
            "event": "shared_u1_phase_vector_measurement",
            "schema_version": 1,
            "satellite_id": satellite,
            "prn": prn,
            "source_index": source_index,
            "tracking_sample_counter": counter,
            "raw_sample_counter": raw_counter,
            "frontend_group_delay_samples": self._frontend_group_delay_samples,
            "cn0_db_hz": cno_db_hz,
            "carrier_doppler_hz": doppler_hz,
            "reported_carrier_phase_rads": _finite_float(
                entry.get("carrier_phase_rads")
            ),
            "aggregate_quality_pass_count": aggregate_count,
            "rolling_quality_measurement_count": rolling_count,
            "aggregate_projector_concentration": concentration,
            "desired_spatial_vector": _complex_payload(aggregate_vector),
            "prompt_vs_wrong_code_db": prompt_vs_off_db,
            "weights_changed_inside_correlation_window": weight_changed,
            "quality_pass": quality_pass,
            "independent_per_prn_lcmv": False,
        }

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
            self._logger.warning("Shared-U1 phase vector log write failed: %s", exc)


__all__ = [
    "apply_shared_phase_fanout",
    "PerPrnMeasuredVectorBeamformerBank",
    "SharedU1DesiredVectorMonitor",
    "SharedU1PhaseCompensationBank",
    "gps_l1_ca_code",
    "phase_invariant_coherence",
]
