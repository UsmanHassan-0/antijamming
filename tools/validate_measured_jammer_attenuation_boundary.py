#!/usr/bin/env python3
"""Replay measured bladeRF and jammer captures across attenuation settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from antijamming.dsp.jammer_detection import cold_start_jammer_evidence
from antijamming.gnss.cold_start_acquisition import (
    evaluate_spatial_pcps_consistency,
    noncoherent_spatial_pcps,
)
from antijamming.gnss.cold_start_acquisition_monitor import (
    orthogonal_nullspace_rows,
)


def _load_corrected(path: Path) -> np.ndarray:
    with np.load(path, allow_pickle=False) as capture:
        samples = np.asarray(capture["corrected"], dtype=np.complex64)
    if samples.ndim != 2 or samples.shape[0] != 4:
        raise ValueError(f"{path}: expected corrected shape (4, N)")
    return samples


def _search(
    samples: np.ndarray,
    rows: np.ndarray,
    *,
    sample_starts: tuple[int, ...],
    sample_rate_hz: float,
) -> dict[str, object]:
    histories: dict[int, list[tuple[int, object]]] = {
        prn: [] for prn in range(1, 33)
    }
    for sample_start in sample_starts:
        window = samples[:, sample_start : sample_start + 32_768]
        if window.shape[1] != 32_768:
            raise ValueError("capture is too short for requested replay windows")
        results = noncoherent_spatial_pcps(
            window,
            rows,
            sample_rate_hz=sample_rate_hz,
            prns=tuple(range(1, 33)),
            dwell_count=5,
        )
        for result in results:
            histories[result.prn].append((sample_start, result))

    decisions: dict[str, object] = {}
    accepted: list[int] = []
    for prn, observations in histories.items():
        decision = evaluate_spatial_pcps_consistency(
            observations,
            sample_rate_hz=sample_rate_hz,
            min_observations=len(sample_starts),
        )
        if decision.accepted:
            accepted.append(prn)
        decisions[f"G{prn:02d}"] = {
            "accepted": bool(decision.accepted),
            "reasons": list(decision.reasons),
            "min_peak_to_median_db": float(decision.min_peak_to_median_db),
            "doppler_span_hz": float(decision.doppler_span_hz),
            "code_phase_slope_error_samples_per_s": float(
                decision.code_phase_slope_error_samples_per_s
            ),
            "code_phase_fit_max_residual_samples": float(
                decision.code_phase_fit_max_residual_samples
            ),
            "min_projected_vector_coherence": float(
                decision.min_projected_vector_coherence
            ),
        }
    return {
        "accepted_prns": accepted,
        "decisions": decisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desired", required=True, type=Path)
    parser.add_argument("--jammer-pad20", required=True, type=Path)
    parser.add_argument("--noise", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--desired-gain-delta-db", type=float, default=10.576)
    args = parser.parse_args()

    desired = _load_corrected(args.desired)
    jammer = _load_corrected(args.jammer_pad20)
    noise = _load_corrected(args.noise)
    usable = min(desired.shape[1], jammer.shape[1], noise.shape[1])
    desired = desired[:, :usable]
    jammer = jammer[:, :usable]
    noise = noise[:, :usable]
    sample_rate_hz = 4_000_000.0
    sample_starts = (0, 262_144, 524_288, 786_432)

    desired_scale = 10.0 ** (float(args.desired_gain_delta_db) / 20.0)
    desired_scaled = desired * desired_scale
    jammer_covariance = jammer @ jammer.conj().T / float(usable)
    eigenvalues, eigenvectors = np.linalg.eigh(jammer_covariance)
    dominant_vector = np.asarray(
        eigenvectors[:, int(np.argmax(eigenvalues))], dtype=np.complex128
    )
    jammer_null_rows = orthogonal_nullspace_rows(dominant_vector[:, None])
    noise_reference_power = float(np.mean(np.abs(noise) ** 2))

    settings: dict[str, object] = {}
    expected_boundary_passed = True
    for target_attenuation_db in (20, 50):
        relative_attenuation_db = target_attenuation_db - 20
        jammer_scaled = jammer * 10.0 ** (-relative_attenuation_db / 20.0)
        mixed = desired_scaled + jammer_scaled
        input_js_db = float(
            10.0
            * np.log10(
                np.mean(np.abs(jammer_scaled) ** 2)
                / np.mean(np.abs(desired_scaled) ** 2)
            )
        )
        evidence = cold_start_jammer_evidence(
            mixed[:, :32_768],
            min_dominant_fraction=0.90,
            min_eigen_gap_db=10.0,
            min_strongest_bin_fraction=0.60,
            min_peak_over_median_db=20.0,
            reference_power_linear=noise_reference_power,
            min_wideband_excess_power_db=20.0,
        )
        raw_search = _search(
            mixed,
            np.eye(4, dtype=np.complex128),
            sample_starts=sample_starts,
            sample_rate_hz=sample_rate_hz,
        )
        null_search = _search(
            mixed,
            jammer_null_rows,
            sample_starts=sample_starts,
            sample_rate_hz=sample_rate_hz,
        )
        settings[str(target_attenuation_db)] = {
            "target_jammer_attenuation_db": target_attenuation_db,
            "input_js_db": input_js_db,
            "mixed_input_power_linear": float(np.mean(np.abs(mixed) ** 2)),
            "detector": {
                "narrowband_detected": bool(evidence.detected),
                "wideband_high_power_candidate": bool(
                    evidence.wideband_high_power_candidate
                ),
                "dominant_fraction": float(evidence.dominant_fraction),
                "dominant_over_second_db": float(
                    evidence.dominant_over_second_db
                ),
                "input_power_over_noise_reference_db": (
                    float(evidence.input_power_over_reference_db)
                    if evidence.input_power_over_reference_db is not None
                    else None
                ),
            },
            "raw_sensor_search": raw_search,
            "measured_rank1_jammer_null_search": null_search,
        }
        if target_attenuation_db == 20:
            expected_boundary_passed &= not bool(
                raw_search["accepted_prns"] or null_search["accepted_prns"]
            )
        else:
            expected_boundary_passed &= bool(
                raw_search["accepted_prns"] and null_search["accepted_prns"]
            )

    payload = {
        "schema_version": 1,
        "purpose": (
            "measured-capture replay proving the acquisition boundary between "
            "20 dB and 50 dB jammer attenuation"
        ),
        "passed": bool(expected_boundary_passed),
        "inputs": {
            "desired_capture": str(args.desired),
            "jammer_pad20_capture": str(args.jammer_pad20),
            "noise_capture": str(args.noise),
            "desired_gain_delta_db": float(args.desired_gain_delta_db),
            "noise_reference_power_linear": noise_reference_power,
            "sample_starts": list(sample_starts),
        },
        "settings": settings,
        "limitations": [
            "This is a linear replay of separately measured captures, not a simultaneous RF capture.",
            "The desired gain delta scales the complete desired capture, including its receiver noise.",
            "The measured jammer-only dominant eigenvector supplies the rank-one null.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "output": str(args.output),
                "summary": {
                    attenuation: {
                        "input_js_db": value["input_js_db"],
                        "raw_accepted_prns": value["raw_sensor_search"][
                            "accepted_prns"
                        ],
                        "null_accepted_prns": value[
                            "measured_rank1_jammer_null_search"
                        ]["accepted_prns"],
                    }
                    for attenuation, value in settings.items()
                },
            },
            indent=2,
        )
    )
    return 0 if expected_boundary_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
