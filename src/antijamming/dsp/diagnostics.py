"""Proof-oriented IQ power and reduction diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
import math

import numpy as np


POWER_EPS = 1e-30


def signal_power_metrics(
    samples: np.ndarray,
    *,
    prefix: str,
    component_threshold: float = 0.98,
) -> dict[str, object]:
    """Return power, RMS, peak, and near-full-scale metrics for complex IQ."""

    values = np.asarray(samples)
    if values.size == 0:
        return {
            f"{prefix}_sample_count": 0,
            f"{prefix}_power_linear": None,
            f"{prefix}_rms_complex": None,
            f"{prefix}_power_db": None,
            f"{prefix}_rms_db": None,
            f"{prefix}_peak_component": None,
            f"{prefix}_peak_magnitude": None,
            f"{prefix}_near_full_scale_pct": None,
        }

    magnitudes = np.abs(values).astype(np.float64, copy=False)
    real_abs = np.abs(values.real).astype(np.float64, copy=False)
    imag_abs = np.abs(values.imag).astype(np.float64, copy=False)
    component_abs = np.maximum(real_abs, imag_abs)
    sample_count = int(values.size)
    power = float(np.mean(magnitudes**2))
    rms = float(math.sqrt(max(power, 0.0)))
    threshold = max(0.0, float(component_threshold))
    near_full_scale_pct = (
        100.0 * float(np.count_nonzero(component_abs >= threshold)) / max(sample_count, 1)
    )
    return {
        f"{prefix}_sample_count": sample_count,
        f"{prefix}_power_linear": power,
        f"{prefix}_rms_complex": rms,
        f"{prefix}_power_db": power_db(power),
        f"{prefix}_rms_db": rms_db(rms),
        f"{prefix}_peak_component": float(np.max(component_abs)),
        f"{prefix}_peak_magnitude": float(np.max(magnitudes)),
        f"{prefix}_near_full_scale_pct": near_full_scale_pct,
    }


def channel_power_metrics(
    samples: np.ndarray,
    *,
    prefix: str,
    component_threshold: float = 0.98,
) -> dict[str, object]:
    """Return per-channel IQ power metrics for a [channels, samples] block."""

    values = np.asarray(samples)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        values = np.zeros((0, 0), dtype=np.complex64)

    channel_count = int(values.shape[0])
    metrics: dict[str, object] = {f"{prefix}_channel_count": channel_count}
    powers: list[float] = []
    powers_db: list[float] = []
    for channel in range(channel_count):
        ch_metrics = signal_power_metrics(
            values[channel],
            prefix=f"{prefix}_ch{channel}",
            component_threshold=component_threshold,
        )
        metrics.update(ch_metrics)
        power = ch_metrics.get(f"{prefix}_ch{channel}_power_linear")
        if isinstance(power, (int, float)) and math.isfinite(float(power)):
            powers.append(float(power))
            powers_db.append(power_db(float(power)))
        else:
            powers.append(float("nan"))
            powers_db.append(float("nan"))

    finite_powers = np.asarray(
        [value for value in powers if math.isfinite(value)],
        dtype=np.float64,
    )
    finite_power_db = np.asarray(
        [value for value in powers_db if math.isfinite(value)],
        dtype=np.float64,
    )
    metrics[f"{prefix}_avg_channel_power_linear"] = (
        float(np.mean(finite_powers)) if finite_powers.size else None
    )
    metrics[f"{prefix}_sum_channel_power_linear"] = (
        float(np.sum(finite_powers)) if finite_powers.size else None
    )
    metrics[f"{prefix}_max_channel_power_linear"] = (
        float(np.max(finite_powers)) if finite_powers.size else None
    )
    metrics[f"{prefix}_min_channel_power_linear"] = (
        float(np.min(finite_powers)) if finite_powers.size else None
    )
    metrics[f"{prefix}_power_spread_db"] = (
        float(np.max(finite_power_db) - np.min(finite_power_db))
        if finite_power_db.size
        else None
    )
    metrics[f"{prefix}_strongest_channel"] = _channel_index(powers, strongest=True)
    metrics[f"{prefix}_weakest_channel"] = _channel_index(powers, strongest=False)
    metrics[f"{prefix}_channel_powers_linear"] = [
        float(value) if math.isfinite(value) else None for value in powers
    ]
    metrics[f"{prefix}_channel_powers_db"] = [
        float(value) if math.isfinite(value) else None for value in powers_db
    ]
    return metrics


def output_reduction_metrics(
    *,
    raw_channel_powers_linear: Sequence[float | None],
    uniform_output_power_linear: float | None,
    lcmv_output_power_linear: float | None,
) -> dict[str, object]:
    """Return explicit total-output reductions; not jammer-only suppression."""

    raw_powers = [
        float(value)
        for value in raw_channel_powers_linear
        if value is not None and math.isfinite(float(value))
    ]
    raw_avg = float(np.mean(raw_powers)) if raw_powers else None
    raw_sum = float(np.sum(raw_powers)) if raw_powers else None
    metrics: dict[str, object] = {
        "measured_output_reduction_vs_uniform_db": ratio_db(
            uniform_output_power_linear,
            lcmv_output_power_linear,
        ),
        "measured_output_reduction_vs_raw_avg_channel_db": ratio_db(
            raw_avg,
            lcmv_output_power_linear,
        ),
        "measured_output_reduction_vs_raw_sum_channels_db": ratio_db(
            raw_sum,
            lcmv_output_power_linear,
        ),
        "uniform_vs_raw_avg_channel_db": ratio_db(raw_avg, uniform_output_power_linear),
        "uniform_vs_raw_sum_channels_db": ratio_db(raw_sum, uniform_output_power_linear),
        "lcmv_vs_uniform_power_ratio_linear": linear_ratio(
            lcmv_output_power_linear,
            uniform_output_power_linear,
        ),
        "lcmv_vs_raw_avg_power_ratio_linear": linear_ratio(
            lcmv_output_power_linear,
            raw_avg,
        ),
        "suppression_db_alias_of": "measured_output_reduction_vs_uniform_db",
    }
    for idx, raw_power in enumerate(raw_channel_powers_linear):
        metrics[f"measured_output_reduction_vs_raw_ch{idx}_db"] = ratio_db(
            raw_power,
            lcmv_output_power_linear,
        )
    return metrics


def normalize_complex_vector(values: object) -> np.ndarray:
    """Return a unit-norm complex vector, or an empty vector when invalid."""

    vector = np.asarray(values, dtype=np.complex128).reshape(-1)
    if vector.size == 0 or not np.all(np.isfinite(vector)):
        return np.zeros((0,), dtype=np.complex128)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0.0:
        return np.zeros((0,), dtype=np.complex128)
    return vector / norm


def align_complex_vector_phase(
    reference: object,
    measured: object,
) -> dict[str, object]:
    """Align measured vector's arbitrary eigenvector phase to a reference."""

    ref = normalize_complex_vector(reference)
    meas = normalize_complex_vector(measured)
    if ref.size == 0 or meas.size == 0 or ref.size != meas.size:
        return {
            "available": False,
            "reason": "empty_or_mismatched_vector",
            "reference_norm": complex_vector_payload(ref),
            "measured_norm": complex_vector_payload(meas),
            "measured_aligned": complex_vector_payload([]),
            "alignment_phase_deg": None,
        }

    inner = complex(np.vdot(ref, meas))
    if abs(inner) <= 0.0:
        phase = 0.0
    else:
        phase = float(np.angle(inner))
    aligned = meas * np.exp(-1j * phase)
    return {
        "available": True,
        "reference_norm": complex_vector_payload(ref),
        "measured_norm": complex_vector_payload(meas),
        "measured_aligned": complex_vector_payload(aligned),
        "alignment_phase_deg": _finite_float(np.degrees(phase)),
    }


def spatial_vector_coherence_metrics(
    reference: object,
    measured: object,
) -> dict[str, object]:
    """Return ideal-vs-measured unit-vector coherence and per-channel errors."""

    ref = normalize_complex_vector(reference)
    meas = normalize_complex_vector(measured)
    if ref.size == 0 or meas.size == 0 or ref.size != meas.size:
        return {
            "ideal_measured_vector_available": False,
            "ideal_measured_vector_reason": "empty_or_mismatched_vector",
            "ideal_measured_coherence_complex": {"real": None, "imag": None},
            "ideal_measured_coherence_abs": None,
            "ideal_measured_coherence_power": None,
            "ideal_measured_mismatch_power": None,
            "ideal_measured_mismatch_db": None,
            "ideal_measured_principal_angle_deg": None,
            "ideal_measured_alignment_phase_deg": None,
            "u1_aligned_minus_ideal": complex_vector_payload([]),
            "u1_aligned_over_ideal_phase_diff_deg": [],
            "u1_aligned_over_ideal_mag_ratio": [],
        }

    inner = complex(np.vdot(ref, meas))
    coherence_abs = min(1.0, max(0.0, float(abs(inner))))
    coherence_power = coherence_abs**2
    mismatch_power = max(0.0, 1.0 - coherence_power)
    phase = float(np.angle(inner)) if abs(inner) > 0.0 else 0.0
    aligned = meas * np.exp(-1j * phase)
    diff = aligned - ref
    phase_diff = np.degrees(np.angle(aligned * np.conj(ref)))
    mag_ratio = np.abs(aligned) / np.maximum(np.abs(ref), POWER_EPS)
    principal_angle = float(np.degrees(np.arccos(coherence_abs)))
    return {
        "ideal_measured_vector_available": True,
        "ideal_measured_coherence_complex": {
            "real": _finite_float(np.real(inner)),
            "imag": _finite_float(np.imag(inner)),
        },
        "ideal_measured_coherence_abs": _finite_float(coherence_abs),
        "ideal_measured_coherence_power": _finite_float(coherence_power),
        "ideal_measured_mismatch_power": _finite_float(mismatch_power),
        "ideal_measured_mismatch_db": power_db(mismatch_power),
        "ideal_measured_principal_angle_deg": _finite_float(principal_angle),
        "ideal_measured_alignment_phase_deg": _finite_float(np.degrees(phase)),
        "u1_aligned_minus_ideal": complex_vector_payload(diff),
        "u1_aligned_over_ideal_phase_diff_deg": [
            _finite_float(value) for value in phase_diff.reshape(-1)
        ],
        "u1_aligned_over_ideal_mag_ratio": [
            _finite_float(value) for value in mag_ratio.reshape(-1)
        ],
    }


def beamformer_vector_response(weights: object, vector: object) -> complex | None:
    """Return vector^H weights for this runtime's weights.conj() @ samples convention."""

    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    v = normalize_complex_vector(vector)
    if w.size == 0 or v.size == 0 or w.size != v.size:
        return None
    if not np.all(np.isfinite(w)):
        return None
    return complex(np.vdot(v, w))


def component_power_after_beamformer(
    *,
    component_power_before: float | None,
    weights: object,
    vector: object,
) -> dict[str, object]:
    """Return component response and predicted post-beamformer power."""

    response = beamformer_vector_response(weights, vector)
    before = _finite_float(component_power_before)
    if response is None or before is None:
        return {
            "response_abs": None,
            "response_power": None,
            "power_before_linear": before,
            "power_before_db": power_db(before),
            "power_after_linear": None,
            "power_after_db": None,
            "suppression_db": None,
        }
    response_power = float(abs(response) ** 2)
    after = float(before * response_power)
    return {
        "response_abs": _finite_float(abs(response)),
        "response_power": _finite_float(response_power),
        "power_before_linear": before,
        "power_before_db": power_db(before),
        "power_after_linear": _finite_float(after),
        "power_after_db": power_db(after),
        "suppression_db": ratio_db(before, after),
    }


def covariance_output_power(
    *,
    covariance: object,
    weights: object,
) -> float | None:
    """Return predicted output power w^H R w from a measured covariance matrix."""

    cov = np.asarray(covariance, dtype=np.complex128)
    w = np.asarray(weights, dtype=np.complex128).reshape(-1)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1] or cov.shape[0] != w.size:
        return None
    if not np.all(np.isfinite(cov)) or not np.all(np.isfinite(w)):
        return None
    power = float(np.real(np.vdot(w, cov @ w)))
    if not math.isfinite(power):
        return None
    return max(power, 0.0)


def power_db(power_linear: float | None) -> float | None:
    if power_linear is None:
        return None
    power = float(power_linear)
    if not math.isfinite(power):
        return None
    return 10.0 * math.log10(max(power, POWER_EPS))


def rms_db(rms_linear: float | None) -> float | None:
    if rms_linear is None:
        return None
    rms = float(rms_linear)
    if not math.isfinite(rms):
        return None
    return 20.0 * math.log10(max(rms, math.sqrt(POWER_EPS)))


def ratio_db(numerator_power: float | None, denominator_power: float | None) -> float | None:
    if numerator_power is None or denominator_power is None:
        return None
    numerator = float(numerator_power)
    denominator = float(denominator_power)
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return None
    return 10.0 * math.log10(max(numerator, POWER_EPS) / max(denominator, POWER_EPS))


def linear_ratio(numerator_power: float | None, denominator_power: float | None) -> float | None:
    if numerator_power is None or denominator_power is None:
        return None
    numerator = float(numerator_power)
    denominator = float(denominator_power)
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return None
    return max(numerator, POWER_EPS) / max(denominator, POWER_EPS)


def complex_vector_payload(values: object) -> dict[str, list[float | None]]:
    arr = np.asarray(values, dtype=np.complex128).reshape(-1)
    return {
        "real": [_finite_float(np.real(value)) for value in arr],
        "imag": [_finite_float(np.imag(value)) for value in arr],
        "magnitude": [_finite_float(abs(value)) for value in arr],
        "phase_deg": [_finite_float(np.degrees(np.angle(value))) for value in arr],
    }


def _channel_index(powers: Sequence[float], *, strongest: bool) -> int | None:
    finite = [(idx, float(power)) for idx, power in enumerate(powers) if math.isfinite(power)]
    if not finite:
        return None
    key = max if strongest else min
    return int(key(finite, key=lambda item: item[1])[0])


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


__all__ = [
    "POWER_EPS",
    "align_complex_vector_phase",
    "beamformer_vector_response",
    "channel_power_metrics",
    "complex_vector_payload",
    "component_power_after_beamformer",
    "covariance_output_power",
    "linear_ratio",
    "normalize_complex_vector",
    "output_reduction_metrics",
    "power_db",
    "ratio_db",
    "rms_db",
    "signal_power_metrics",
    "spatial_vector_coherence_metrics",
]
