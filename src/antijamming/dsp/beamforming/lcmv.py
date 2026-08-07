"""One-null LCMV test weights for the realtime array combiner."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from antijamming.dsp.doa.music import steering_vector
from antijamming.dsp.models import internal_angle_to_operator_bearing_deg


RESPONSE_DB_EPS = 1e-300


@dataclass(frozen=True, slots=True)
class LcmvCovarianceNullResult:
    """Weights and diagnostics for one full-covariance LCMV null."""

    weights: np.ndarray
    preserve_vector: np.ndarray
    null_vector: np.ndarray
    null_angle_deg: float | None
    diagonal_loading: float
    condition_number_R: float
    condition_number: float
    weight_norm: float
    max_weight_abs: float
    preserve_target: complex
    preserve_response: complex
    null_response: complex
    preserve_residual: complex
    null_residual: complex


@dataclass(frozen=True, slots=True)
class LcmvModelResponse:
    """Absolute, unnormalized model response diagnostics for one weight vector."""

    scan_internal_angles_deg: np.ndarray
    scan_display_bearings_deg: np.ndarray
    response_abs: np.ndarray
    response_power: np.ndarray
    response_db: np.ndarray
    response_power_db: np.ndarray
    response_db_epsilon: float
    closest_grid_bearing_to_selected_null_deg: float | None
    selected_null_grid_error_deg: float | None
    model_response_at_selected_null_abs: float | None
    model_response_at_selected_null_db: float | None
    model_response_power_at_selected_null_db: float | None
    model_min_response_abs: float | None
    model_min_response_db: float | None
    model_min_response_bearing_deg: float | None
    model_max_response_abs: float | None
    model_max_response_db: float | None
    model_max_response_bearing_deg: float | None


def covariance_lcmv_ideal_null_weights(
    *,
    covariance: np.ndarray,
    n_channels: int,
    null_angle_deg: float,
    rf_freq_hz: float,
    array_spacing_m: float,
    preserve_vector: np.ndarray | None = None,
    diagonal_loading_rel: float = 1e-3,
    diagonal_loading_abs: float = 0.0,
    condition_number_limit: float = 1e8,
    max_weight_norm: float = 8.0,
) -> LcmvCovarianceNullResult:
    """Return full-covariance LCMV weights using an ideal steering null."""

    n = int(n_channels)
    if n != 4:
        raise ValueError(f"covariance LCMV steering model expects 4 channels, got {n}")
    null_angle = float(null_angle_deg) % 360.0
    if not np.isfinite(null_angle):
        raise ValueError("covariance LCMV null angle is not finite")
    null_steering = np.asarray(
        steering_vector(
            np.asarray([null_angle], dtype=np.float64),
            float(rf_freq_hz),
            float(array_spacing_m),
        ),
        dtype=np.complex128,
    ).reshape(-1)
    if null_steering.size != n:
        raise ValueError(
            f"covariance LCMV steering vector size {null_steering.size} does not match {n}"
        )
    result = _covariance_lcmv_constraint_weights(
        covariance=covariance,
        preserve_vector=preserve_vector,
        null_vector=null_steering,
        diagonal_loading_rel=diagonal_loading_rel,
        diagonal_loading_abs=diagonal_loading_abs,
        condition_number_limit=condition_number_limit,
        max_weight_norm=max_weight_norm,
    )
    return LcmvCovarianceNullResult(null_angle_deg=null_angle, **result)


def covariance_lcmv_vector_null_weights(
    *,
    covariance: np.ndarray,
    null_vector: np.ndarray,
    preserve_vector: np.ndarray | None = None,
    diagonal_loading_rel: float = 1e-3,
    diagonal_loading_abs: float = 0.0,
    condition_number_limit: float = 1e8,
    max_weight_norm: float = 8.0,
) -> LcmvCovarianceNullResult:
    """Return full-covariance LCMV weights using a measured complex vector null."""

    vector = np.asarray(null_vector, dtype=np.complex128).reshape(-1)
    n = int(vector.size)
    if n == 0:
        raise ValueError("covariance measured-vector LCMV null vector is empty")
    if not np.all(np.isfinite(vector)):
        raise ValueError("covariance measured-vector LCMV null vector contains NaN or Inf")
    vector_norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector_norm) or vector_norm <= 0.0:
        raise ValueError("covariance measured-vector LCMV null vector has zero norm")
    result = _covariance_lcmv_constraint_weights(
        covariance=covariance,
        preserve_vector=preserve_vector,
        null_vector=vector,
        diagonal_loading_rel=diagonal_loading_rel,
        diagonal_loading_abs=diagonal_loading_abs,
        condition_number_limit=condition_number_limit,
        max_weight_norm=max_weight_norm,
    )
    return LcmvCovarianceNullResult(null_angle_deg=None, **result)


def _covariance_lcmv_constraint_weights(
    *,
    covariance: np.ndarray,
    preserve_vector: np.ndarray | None,
    null_vector: np.ndarray,
    diagonal_loading_rel: float,
    diagonal_loading_abs: float,
    condition_number_limit: float,
    max_weight_norm: float,
) -> dict[str, object]:
    cov = np.asarray(covariance, dtype=np.complex128)
    null_vector = np.asarray(null_vector, dtype=np.complex128).reshape(-1)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValueError(f"covariance LCMV covariance must be square, got {cov.shape}")
    n = int(cov.shape[0])
    if null_vector.size != n:
        raise ValueError(
            f"covariance LCMV null vector size {null_vector.size} does not match {n}"
        )
    preserve = (
        np.ones((n,), dtype=np.complex128)
        if preserve_vector is None
        else np.asarray(preserve_vector, dtype=np.complex128).reshape(-1)
    )
    if preserve.size != n:
        raise ValueError(
            f"covariance LCMV preserve vector size {preserve.size} does not match {n}"
        )
    if not np.all(np.isfinite(cov)) or not np.all(np.isfinite(preserve)):
        raise ValueError("covariance LCMV inputs contain NaN or Inf")
    if not np.all(np.isfinite(null_vector)):
        raise ValueError("covariance LCMV null vector contains NaN or Inf")
    preserve_norm_value = float(np.linalg.norm(preserve))
    null_norm_value = float(np.linalg.norm(null_vector))
    if not np.isfinite(preserve_norm_value) or preserve_norm_value <= 0.0:
        raise ValueError("covariance LCMV preserve vector has zero norm")
    if not np.isfinite(null_norm_value) or null_norm_value <= 0.0:
        raise ValueError("covariance LCMV null vector has zero norm")
    preserve_norm = preserve / preserve_norm_value
    null_norm = null_vector / null_norm_value
    constraints = np.column_stack((preserve_norm, null_norm))
    reference_weights = np.ones((n,), dtype=np.complex128)
    preserve_target = complex(np.vdot(preserve_norm, reference_weights))
    targets = np.asarray([preserve_target, 0.0 + 0.0j], dtype=np.complex128)
    loading = _diagonal_loading_value(
        covariance=cov,
        diagonal_loading_rel=diagonal_loading_rel,
        diagonal_loading_abs=diagonal_loading_abs,
    )
    loaded = cov + loading * np.eye(n, dtype=np.complex128)
    cond_r = float(np.linalg.cond(loaded))
    limit = float(condition_number_limit)
    if not np.isfinite(cond_r) or cond_r > limit:
        raise ValueError(
            "covariance LCMV loaded covariance ill-conditioned: "
            f"cond={cond_r:.3e} limit={limit:.3e}"
        )
    try:
        rinv_c = np.linalg.solve(loaded, constraints)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"covariance LCMV R solve failed: {exc}") from exc
    constraint_gram = constraints.conj().T @ rinv_c
    condition_number = float(np.linalg.cond(constraint_gram))
    if not np.isfinite(condition_number) or condition_number > limit:
        raise ValueError(
            "covariance LCMV constraint matrix ill-conditioned: "
            f"cond={condition_number:.3e} limit={limit:.3e}"
        )
    try:
        weights = rinv_c @ np.linalg.solve(constraint_gram, targets)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"covariance LCMV constraint solve failed: {exc}") from exc
    weights = _validated_weight_vector(
        weights,
        n_channels=n,
        max_weight_norm=max_weight_norm,
        label="covariance LCMV",
    )
    preserve_response = complex(np.vdot(preserve_norm, weights))
    null_response = complex(np.vdot(null_norm, weights))
    return {
        "weights": weights,
        "preserve_vector": preserve_norm,
        "null_vector": null_norm,
        "diagonal_loading": float(loading),
        "condition_number_R": cond_r,
        "condition_number": condition_number,
        "weight_norm": float(np.linalg.norm(weights)),
        "max_weight_abs": float(np.max(np.abs(weights))),
        "preserve_target": preserve_target,
        "preserve_response": preserve_response,
        "null_response": null_response,
        "preserve_residual": preserve_response - preserve_target,
        "null_residual": null_response,
    }


def _diagonal_loading_value(
    *,
    covariance: np.ndarray,
    diagonal_loading_rel: float,
    diagonal_loading_abs: float,
) -> float:
    cov = np.asarray(covariance, dtype=np.complex128)
    n = max(int(cov.shape[0]), 1)
    rel = max(0.0, float(diagonal_loading_rel))
    absolute = max(0.0, float(diagonal_loading_abs))
    trace_mean = float(np.real(np.trace(cov)) / float(n))
    if not np.isfinite(trace_mean) or trace_mean < 0.0:
        trace_mean = 0.0
    return float(absolute + rel * trace_mean)


def _validated_weight_vector(
    weights: np.ndarray,
    *,
    n_channels: int,
    max_weight_norm: float,
    label: str,
) -> np.ndarray:
    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    n = int(n_channels)
    if w.shape != (n,):
        raise ValueError(f"{label} weights shape {w.shape} does not match {(n,)}")
    if not np.all(np.isfinite(w)):
        raise ValueError(f"{label} weights contain NaN or Inf")
    weight_norm = float(np.linalg.norm(w))
    norm_limit = float(max_weight_norm)
    if not np.isfinite(weight_norm) or weight_norm > norm_limit:
        raise ValueError(f"{label} weight norm {weight_norm:.3f} exceeds limit {norm_limit:.3f}")
    return w


def lcmv_model_response(
    *,
    weights: np.ndarray,
    scan_angles_deg: np.ndarray,
    rf_freq_hz: float,
    array_spacing_m: float,
    selected_null_angle_deg: float | None = None,
    response_db_epsilon: float = RESPONSE_DB_EPS,
) -> LcmvModelResponse:
    """Return absolute model response arrays; no normalization is applied."""

    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    scan_internal = np.asarray(scan_angles_deg, dtype=np.float64).reshape(-1)
    if w.size == 0 or scan_internal.size == 0:
        empty = np.zeros((0,), dtype=np.float64)
        return LcmvModelResponse(
            scan_internal_angles_deg=empty,
            scan_display_bearings_deg=empty,
            response_abs=empty,
            response_power=empty,
            response_db=empty,
            response_power_db=empty,
            response_db_epsilon=float(response_db_epsilon),
            closest_grid_bearing_to_selected_null_deg=None,
            selected_null_grid_error_deg=None,
            model_response_at_selected_null_abs=None,
            model_response_at_selected_null_db=None,
            model_response_power_at_selected_null_db=None,
            model_min_response_abs=None,
            model_min_response_db=None,
            model_min_response_bearing_deg=None,
            model_max_response_abs=None,
            model_max_response_db=None,
            model_max_response_bearing_deg=None,
        )

    steering = steering_vector(
        scan_internal,
        float(rf_freq_hz),
        float(array_spacing_m),
    )
    response_abs = np.asarray(np.abs(w.conj() @ steering), dtype=np.float64)
    response_power = np.asarray(response_abs**2, dtype=np.float64)
    eps = max(float(response_db_epsilon), np.finfo(np.float64).tiny)
    response_db = np.asarray(20.0 * np.log10(np.maximum(response_abs, eps)), dtype=np.float64)
    response_power_db = np.asarray(
        10.0 * np.log10(np.maximum(response_power, eps)),
        dtype=np.float64,
    )
    display = np.asarray(
        [internal_angle_to_operator_bearing_deg(float(angle)) for angle in scan_internal],
        dtype=np.float64,
    )
    min_idx = int(np.argmin(response_abs)) if response_abs.size else None
    max_idx = int(np.argmax(response_abs)) if response_abs.size else None
    selected_idx: int | None = None
    grid_error: float | None = None
    if selected_null_angle_deg is not None and np.isfinite(float(selected_null_angle_deg)):
        distance = np.abs((scan_internal - float(selected_null_angle_deg) + 180.0) % 360.0 - 180.0)
        selected_idx = int(np.argmin(distance))
        grid_error = float(distance[selected_idx])

    return LcmvModelResponse(
        scan_internal_angles_deg=scan_internal,
        scan_display_bearings_deg=display,
        response_abs=response_abs,
        response_power=response_power,
        response_db=response_db,
        response_power_db=response_power_db,
        response_db_epsilon=eps,
        closest_grid_bearing_to_selected_null_deg=(
            float(display[selected_idx]) if selected_idx is not None else None
        ),
        selected_null_grid_error_deg=grid_error,
        model_response_at_selected_null_abs=(
            float(response_abs[selected_idx]) if selected_idx is not None else None
        ),
        model_response_at_selected_null_db=(
            float(response_db[selected_idx]) if selected_idx is not None else None
        ),
        model_response_power_at_selected_null_db=(
            float(response_power_db[selected_idx]) if selected_idx is not None else None
        ),
        model_min_response_abs=float(response_abs[min_idx]) if min_idx is not None else None,
        model_min_response_db=float(response_db[min_idx]) if min_idx is not None else None,
        model_min_response_bearing_deg=float(display[min_idx]) if min_idx is not None else None,
        model_max_response_abs=float(response_abs[max_idx]) if max_idx is not None else None,
        model_max_response_db=float(response_db[max_idx]) if max_idx is not None else None,
        model_max_response_bearing_deg=float(display[max_idx]) if max_idx is not None else None,
    )
