#!/usr/bin/env python3
"""Create deterministic known-truth evidence for jammer-first acquisition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from antijamming.gnss.cold_start_acquisition import (
    SpatialPcpsResult,
    noncoherent_spatial_pcps,
    sampled_gps_l1_ca_code,
)
from antijamming.gnss.shared_u1_phase_compensation import phase_invariant_coherence


def _steering(angle_deg: float, sensor_count: int = 4) -> np.ndarray:
    sensors = np.arange(sensor_count, dtype=np.float64)
    return np.exp(1j * np.pi * sensors * np.sin(np.deg2rad(angle_deg)))


def _nullspace_rows(null_vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(null_vectors, dtype=np.complex128)
    left, singular_values, _ = np.linalg.svd(vectors, full_matrices=True)
    tolerance = (
        max(vectors.shape)
        * np.finfo(np.float64).eps
        * float(singular_values[0])
    )
    rank = int(np.count_nonzero(singular_values > tolerance))
    return np.asarray(
        np.sqrt(vectors.shape[0]) * left[:, rank:].T,
        dtype=np.complex128,
    )


def _row_payload(result: SpatialPcpsResult) -> dict[str, object]:
    return {
        "prn": result.prn,
        "doppler_hz": result.doppler_hz,
        "code_phase_samples": result.code_phase_samples,
        "peak_to_median_db": result.peak_to_median_db,
        "peak_to_mean_db": result.peak_to_mean_db,
        "spatial_projector_dominant_fraction": (
            result.spatial_projector_dominant_fraction
        ),
        "projected_spatial_vector": [
            {"real": float(value.real), "imag": float(value.imag)}
            for value in result.projected_spatial_vector
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    sample_rate_hz = 4_000_000.0
    samples_per_ms = 4_000
    dwell_count = 5
    sample_count = samples_per_ms * dwell_count
    time_axis = np.arange(sample_count, dtype=np.float64) / sample_rate_hz
    rng = np.random.default_rng(20_260_823)
    raw = (
        rng.normal(size=(4, sample_count))
        + 1j * rng.normal(size=(4, sample_count))
    ) / np.sqrt(2.0)
    truths: dict[int, dict[str, float | int]] = {
        14: {
            "amplitude": 0.050,
            "doppler_hz": -4_250.0,
            "code_phase_samples": 731,
            "spatial_angle_deg": 12.0,
        },
        22: {
            "amplitude": 0.040,
            "doppler_hz": 2_500.0,
            "code_phase_samples": 2_511,
            "spatial_angle_deg": 68.0,
        },
    }
    for prn, truth in truths.items():
        one_ms = sampled_gps_l1_ca_code(
            prn,
            sample_rate_hz=sample_rate_hz,
            sample_count=samples_per_ms,
        )
        code = np.roll(
            np.tile(one_ms, dwell_count), int(truth["code_phase_samples"])
        )
        signal = code * np.exp(
            2j * np.pi * float(truth["doppler_hz"]) * time_axis
        )
        raw += (
            float(truth["amplitude"])
            * _steering(float(truth["spatial_angle_deg"]))[:, None]
            * signal[None, :]
        )

    jammer_one = _steering(-34.0)
    jammer_two = _steering(47.0)
    jammer_one_amplitude = 18.0
    jammer_two_amplitude = 8.0
    raw += (
        jammer_one_amplitude
        * jammer_one[:, None]
        * np.exp(-2j * np.pi * 372_070.3125 * time_axis)[None, :]
    )
    symbols = rng.choice((-1.0, 1.0), size=sample_count // 8 + 1)
    waveform = np.repeat(symbols, 8)[:sample_count] * np.exp(
        2j * np.pi * 615_000.0 * time_axis
    )
    raw += jammer_two_amplitude * jammer_two[:, None] * waveform[None, :]
    raw = np.asarray(raw, dtype=np.complex64)

    truth_jammer_subspace = np.column_stack((jammer_one, jammer_two))
    exact_rows = _nullspace_rows(truth_jammer_subspace)
    covariance = raw @ raw.conj().T / float(sample_count)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.real(eigenvalues[order])
    estimated_subspace = np.asarray(eigenvectors[:, order[:2]], dtype=np.complex128)
    estimated_rows = _nullspace_rows(estimated_subspace)
    prns = tuple(range(1, 33))
    searches = {
        "unprotected_raw_sensor_bank": noncoherent_spatial_pcps(
            raw,
            np.eye(4, dtype=np.complex128),
            sample_rate_hz=sample_rate_hz,
            prns=prns,
            dwell_count=dwell_count,
        ),
        "exact_jammer_nullspace_bank": noncoherent_spatial_pcps(
            raw,
            exact_rows,
            sample_rate_hz=sample_rate_hz,
            prns=prns,
            dwell_count=dwell_count,
        ),
        "estimated_jammer_nullspace_bank": noncoherent_spatial_pcps(
            raw,
            estimated_rows,
            sample_rate_hz=sample_rate_hz,
            prns=prns,
            dwell_count=dwell_count,
        ),
    }
    estimated_by_prn = {
        row.prn: row for row in searches["estimated_jammer_nullspace_bank"]
    }
    allowed_projector = (
        np.eye(4) - estimated_subspace @ estimated_subspace.conj().T
    )
    target_checks: dict[str, object] = {}
    all_passed = True
    for prn, truth in truths.items():
        result = estimated_by_prn[prn]
        projected_truth = allowed_projector @ _steering(
            float(truth["spatial_angle_deg"])
        )
        coherence = phase_invariant_coherence(
            result.projected_spatial_vector, projected_truth
        )
        passed = bool(
            result.doppler_hz == float(truth["doppler_hz"])
            and result.code_phase_samples == int(truth["code_phase_samples"])
            and result.peak_to_median_db > 7.5
            and coherence > 0.95
            and result.spatial_projector_dominant_fraction > 0.75
        )
        all_passed = all_passed and passed
        target_checks[str(prn)] = {
            "passed": passed,
            "projected_vector_coherence": coherence,
            "result": _row_payload(result),
            "truth": truth,
            "stronger_jammer_to_signal_db": float(
                20.0
                * np.log10(jammer_one_amplitude / float(truth["amplitude"]))
            ),
        }

    subspace_residual = float(
        np.linalg.norm(allowed_projector @ truth_jammer_subspace)
        / np.linalg.norm(truth_jammer_subspace)
    )
    all_passed = all_passed and subspace_residual < 0.002
    payload: dict[str, object] = {
        "schema_version": 1,
        "purpose": (
            "known-truth proof of jammer-on-before-GNSS acquisition using "
            "common-hypothesis noncoherent spatial PCPS"
        ),
        "passed": bool(all_passed),
        "sample_rate_hz": sample_rate_hz,
        "dwell_count": dwell_count,
        "sensor_count": 4,
        "interference_rank": 2,
        "jammer_spatial_angles_deg": [-34.0, 47.0],
        "estimated_covariance_eigenvalue_fractions": [
            float(value / np.sum(eigenvalues)) for value in eigenvalues
        ],
        "estimated_subspace_truth_residual": subspace_residual,
        "target_checks": target_checks,
        "searches": {
            name: [_row_payload(row) for row in rows]
            for name, rows in searches.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": payload["passed"],
        "output": str(args.output),
        "subspace_residual": subspace_residual,
        "targets": target_checks,
    }, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
