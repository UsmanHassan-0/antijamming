#!/usr/bin/env python3
"""Capture separated X300 windows and test jammer-first PRN consistency."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from antijamming.app.runtime_config import build_runtime_config
from antijamming.dsp.frequency_notch import StatefulComplexBandstop
from antijamming.dsp.jammer_detection import (
    cold_start_jammer_evidence,
    selected_interference_subspace,
)
from antijamming.gnss.cold_start_acquisition import noncoherent_spatial_pcps
from antijamming.gnss.shared_u1_phase_compensation import phase_invariant_coherence
from antijamming.radio.usrp import UsrpRxDevice


def _orthogonal_nullspace_rows(null_vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(null_vectors, dtype=np.complex128)
    left, singular_values, _ = np.linalg.svd(vectors, full_matrices=True)
    tolerance = (
        max(vectors.shape)
        * np.finfo(np.float64).eps
        * max(float(singular_values[0]), 1.0)
    )
    rank = int(np.count_nonzero(singular_values > tolerance))
    return np.asarray(
        np.sqrt(vectors.shape[0]) * left[:, rank:].T,
        dtype=np.complex128,
    )


def _complex_payload(values: np.ndarray) -> list[dict[str, float]]:
    return [
        {"real": float(value.real), "imag": float(value.imag)}
        for value in np.asarray(values).reshape(-1)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--save-iq", required=True, type=Path)
    parser.add_argument("--window-gap-chunks", type=int, default=60)
    parser.add_argument("--window-count", type=int, default=3)
    parser.add_argument("--dwells", type=int, default=5)
    args = parser.parse_args()
    cfg = build_runtime_config()
    gap = max(1, int(args.window_gap_chunks))
    count = max(2, int(args.window_count))
    targets = {index * gap for index in range(count)}
    captured: list[np.ndarray] = []
    sample_starts: list[int] = []
    states: list[str] = []
    device = UsrpRxDevice(cfg)
    try:
        for _ in range(6):
            result = device.recv_chunk()
            states.append(str(result.state))
        relative_sample_start = 0
        for chunk_index in range(max(targets) + 1):
            result = device.recv_chunk()
            states.append(str(result.state))
            if result.got_samples <= 0:
                continue
            chunk = np.asarray(result.chunk, dtype=np.complex64)
            if chunk_index in targets:
                captured.append(np.array(chunk, copy=True))
                sample_starts.append(relative_sample_start)
            relative_sample_start += int(chunk.shape[1])
    finally:
        device.stop()
    if len(captured) != count:
        raise RuntimeError(
            f"captured {len(captured)}/{count} requested windows; states={states[-20:]}"
        )

    iq = np.stack(captured)
    correction = np.asarray(
        cfg.phase_correction_vector
        if cfg.phase_correction_vector is not None
        else np.ones((iq.shape[1],), dtype=np.complex128),
        dtype=np.complex128,
    )
    corrected = np.asarray(iq * correction[None, :, None], dtype=np.complex64)
    args.save_iq.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.save_iq,
        iq=iq,
        corrected=corrected,
        sample_starts=np.asarray(sample_starts, dtype=np.int64),
        sample_rate_hz=float(cfg.sample_rate),
        center_frequency_hz=float(cfg.center_freq_hz),
    )

    window_payloads: list[dict[str, object]] = []
    result_sets: dict[str, list[dict[int, object]]] = {
        "spatial_only": [],
        "notch_then_spatial": [],
    }
    for index, values in enumerate(corrected):
        evidence = cold_start_jammer_evidence(
            values,
            min_dominant_fraction=float(cfg.lcmv_cold_start_min_dominant_fraction),
            min_eigen_gap_db=float(cfg.lcmv_cold_start_min_eigen_gap_db),
            min_strongest_bin_fraction=float(
                cfg.lcmv_cold_start_min_spectral_concentration
            ),
            min_peak_over_median_db=float(
                cfg.lcmv_cold_start_min_peak_over_median_db
            ),
            strongest_bin_fraction=float(
                cfg.lcmv_cold_start_spectral_top_fraction
            ),
        )
        vectors, secondary_gaps = selected_interference_subspace(
            evidence,
            max_rank=int(cfg.lcmv_cold_start_max_interference_rank),
            min_secondary_eigen_gap_db=float(
                cfg.lcmv_cold_start_secondary_eigen_gap_db
            ),
            min_secondary_spectral_concentration=float(
                cfg.lcmv_cold_start_min_spectral_concentration
            ),
            min_secondary_peak_over_median_db=float(
                cfg.lcmv_cold_start_min_peak_over_median_db
            ),
        )
        rows = _orthogonal_nullspace_rows(vectors)
        peak_offset_hz = float(
            evidence.peak_normalized_frequency * cfg.sample_rate
        )
        notch = StatefulComplexBandstop(
            sample_rate_hz=float(cfg.sample_rate),
            bandwidth_hz=float(cfg.lcmv_cold_start_frequency_notch_bandwidth_hz),
            num_taps=int(cfg.lcmv_cold_start_frequency_notch_fir_taps),
        )
        notch.tune(peak_offset_hz)
        notched = notch.process(values)
        discard = max(2 * notch.group_delay_samples, notch.num_taps)
        variants = {
            "spatial_only": (values, int(sample_starts[index])),
            "notch_then_spatial": (
                notched[:, discard:],
                int(sample_starts[index]) + discard,
            ),
        }
        for name, (variant, _) in variants.items():
            results = noncoherent_spatial_pcps(
                variant,
                rows,
                sample_rate_hz=float(cfg.sample_rate),
                prns=tuple(range(1, 33)),
                dwell_count=int(args.dwells),
            )
            result_sets[name].append({row.prn: row for row in results})
        window_payloads.append(
            {
                "window_index": index,
                "sample_start": int(sample_starts[index]),
                "elapsed_s": float(sample_starts[index] / cfg.sample_rate),
                "detected": bool(evidence.detected),
                "dominant_fraction": float(evidence.dominant_fraction),
                "dominant_over_second_db": float(evidence.dominant_over_second_db),
                "selected_rank": int(vectors.shape[1]),
                "secondary_eigen_gaps_db": [float(value) for value in secondary_gaps],
                "peak_offset_hz": peak_offset_hz,
                "interference_vectors": [
                    _complex_payload(vectors[:, vector_index])
                    for vector_index in range(vectors.shape[1])
                ],
            }
        )

    consistency: dict[str, list[dict[str, object]]] = {}
    for name, windows in result_sets.items():
        rows: list[dict[str, object]] = []
        discard = (
            max(
                2 * int(cfg.lcmv_cold_start_frequency_notch_fir_taps // 2),
                int(cfg.lcmv_cold_start_frequency_notch_fir_taps),
            )
            if name == "notch_then_spatial"
            else 0
        )
        effective_starts = np.asarray(sample_starts, dtype=np.float64) + discard
        elapsed = effective_starts / float(cfg.sample_rate)
        for prn in range(1, 33):
            results = [window[prn] for window in windows]
            dopplers = np.asarray([row.doppler_hz for row in results])
            absolute_phases = np.asarray(
                [
                    (row.code_phase_samples + int(effective_starts[i])) % 4000
                    for i, row in enumerate(results)
                ],
                dtype=np.float64,
            )
            unwrapped = np.unwrap(2.0 * np.pi * absolute_phases / 4000.0)
            unwrapped *= 4000.0 / (2.0 * np.pi)
            slope, intercept = np.polyfit(elapsed, unwrapped, 1)
            fitted = slope * elapsed + intercept
            fit_residual = float(np.max(np.abs(unwrapped - fitted)))
            coherences = [
                phase_invariant_coherence(
                    results[0].projected_spatial_vector,
                    row.projected_spatial_vector,
                )
                for row in results[1:]
            ]
            rows.append(
                {
                    "prn": prn,
                    "peak_to_median_db": [
                        float(row.peak_to_median_db) for row in results
                    ],
                    "min_peak_to_median_db": float(
                        min(row.peak_to_median_db for row in results)
                    ),
                    "doppler_hz": [float(value) for value in dopplers],
                    "doppler_span_hz": float(np.ptp(dopplers)),
                    "code_phase_samples": [
                        int(row.code_phase_samples) for row in results
                    ],
                    "absolute_code_phase_samples": [
                        float(value) for value in absolute_phases
                    ],
                    "absolute_code_phase_slope_samples_per_s": float(slope),
                    "absolute_code_phase_fit_max_residual_samples": fit_residual,
                    "projected_vector_coherence_to_first": [
                        float(value) for value in coherences
                    ],
                    "min_projected_vector_coherence": float(
                        min(coherences) if coherences else 1.0
                    ),
                    "projector_dominant_fraction": [
                        float(row.spatial_projector_dominant_fraction)
                        for row in results
                    ],
                }
            )
        consistency[name] = sorted(
            rows,
            key=lambda row: (
                float(row["min_peak_to_median_db"]),
                -float(row["doppler_span_hz"]),
            ),
            reverse=True,
        )

    payload: dict[str, object] = {
        "schema_version": 1,
        "purpose": (
            "negative-control temporal consistency audit for jammer-first "
            "spatial PCPS; no desired transmitter is assumed"
        ),
        "sample_rate_hz": float(cfg.sample_rate),
        "center_frequency_hz": float(cfg.center_freq_hz),
        "samples_per_window": int(corrected.shape[2]),
        "window_count": count,
        "window_gap_chunks": gap,
        "window_payloads": window_payloads,
        "consistency": consistency,
        "saved_iq": str(args.save_iq),
        "device_states_tail": states[-20:],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "saved_iq": str(args.save_iq),
                "windows": window_payloads,
                "spatial_only_top": consistency["spatial_only"][:5],
                "notch_then_spatial_top": consistency["notch_then_spatial"][:5],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
