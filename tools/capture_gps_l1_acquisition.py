#!/usr/bin/env python3
"""Capture four USRP channels and measure GPS L1 C/A acquisition evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy.signal import istft, stft

from antijamming.app.runtime_config import build_runtime_config
from antijamming.dsp.frequency_notch import StatefulComplexBandstop
from antijamming.dsp.jammer_detection import (
    cold_start_jammer_evidence,
    selected_interference_subspace,
)
from antijamming.gnss.shared_u1_phase_compensation import (
    gps_l1_ca_code,
    jammer_nullspace_acquisition_rows,
)
from antijamming.radio.usrp import UsrpRxDevice


def _sampled_code(prn: int, sample_rate_hz: float, sample_count: int) -> np.ndarray:
    chips = gps_l1_ca_code(prn)
    indices = np.floor(
        np.arange(sample_count, dtype=np.float64)
        * 1_023_000.0
        / float(sample_rate_hz)
    ).astype(np.int64)
    return np.asarray(chips[indices % chips.size], dtype=np.complex64)


def _search_stream(
    samples: np.ndarray,
    *,
    sample_rate_hz: float,
    prns: tuple[int, ...],
    dwell_count: int,
) -> list[dict[str, float | int]]:
    samples_per_ms = int(round(float(sample_rate_hz) / 1000.0))
    available_dwells = int(samples.size // samples_per_ms)
    dwells = min(max(1, int(dwell_count)), available_dwells)
    if dwells < 1:
        raise ValueError("capture does not contain one complete millisecond")
    blocks = np.asarray(
        samples[: dwells * samples_per_ms].reshape(dwells, samples_per_ms),
        dtype=np.complex64,
    )
    blocks = blocks - np.mean(blocks, axis=1, keepdims=True)
    time_axis = np.arange(samples_per_ms, dtype=np.float64) / float(sample_rate_hz)
    dopplers = np.arange(-10_000.0, 10_000.1, 250.0, dtype=np.float64)
    carriers = np.exp(-2j * np.pi * dopplers[:, None] * time_axis[None, :])
    spectra = np.fft.fft(
        blocks[:, None, :] * carriers[None, :, :],
        axis=2,
    )
    results: list[dict[str, float | int]] = []
    for prn in prns:
        code = _sampled_code(prn, sample_rate_hz, samples_per_ms)
        correlation = np.fft.ifft(
            spectra * np.conj(np.fft.fft(code))[None, None, :],
            axis=2,
        )
        noncoherent = np.sum(np.abs(correlation) ** 2, axis=0)
        flat_peak = int(np.argmax(noncoherent))
        doppler_index, code_index = np.unravel_index(flat_peak, noncoherent.shape)
        peak = float(noncoherent[doppler_index, code_index])
        median = float(np.median(noncoherent))
        mean = float(np.mean(noncoherent))
        results.append(
            {
                "prn": int(prn),
                "doppler_hz": float(dopplers[doppler_index]),
                "code_phase_samples": int(code_index),
                "peak_to_median_db": float(
                    10.0 * np.log10(max(peak, 1e-30) / max(median, 1e-30))
                ),
                "peak_to_mean_db": float(
                    10.0 * np.log10(max(peak, 1e-30) / max(mean, 1e-30))
                ),
            }
        )
    return sorted(results, key=lambda row: float(row["peak_to_median_db"]), reverse=True)


def _orthogonal_nullspace_rows(null_vectors: np.ndarray) -> np.ndarray:
    """Return a nonredundant equal-noise-gain basis orthogonal to null vectors."""

    vectors = np.asarray(null_vectors, dtype=np.complex128)
    if vectors.ndim == 1:
        vectors = vectors[:, np.newaxis]
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
    # Columns of left[:, rank:] are independent weight vectors w satisfying
    # w^H J=0.  Scale each to the four-element uniform-sum norm sqrt(M), so all
    # compared banks have the same white-noise gain per output.
    basis = np.asarray(left[:, rank:], dtype=np.complex128)
    return np.asarray(np.sqrt(vectors.shape[0]) * basis.T, dtype=np.complex128)


def _search_stream_bank(
    streams: np.ndarray,
    *,
    sample_rate_hz: float,
    prns: tuple[int, ...],
    dwell_count: int,
) -> list[dict[str, float | int]]:
    """Acquire each PRN by noncoherently combining all spatial outputs."""

    values = np.asarray(streams, dtype=np.complex64)
    if values.ndim != 2 or values.shape[0] < 1:
        raise ValueError("acquisition bank must contain one or more streams")
    samples_per_ms = int(round(float(sample_rate_hz) / 1000.0))
    available_dwells = int(values.shape[1] // samples_per_ms)
    dwells = min(max(1, int(dwell_count)), available_dwells)
    if dwells < 1:
        raise ValueError("capture does not contain one complete millisecond")
    blocks = np.asarray(
        values[:, : dwells * samples_per_ms].reshape(
            values.shape[0], dwells, samples_per_ms
        ),
        dtype=np.complex64,
    )
    blocks = blocks - np.mean(blocks, axis=2, keepdims=True)
    time_axis = np.arange(samples_per_ms, dtype=np.float64) / float(sample_rate_hz)
    dopplers = np.arange(-10_000.0, 10_000.1, 250.0, dtype=np.float64)
    carriers = np.exp(-2j * np.pi * dopplers[:, None] * time_axis[None, :])
    spectra = np.fft.fft(
        blocks[:, :, None, :] * carriers[None, None, :, :],
        axis=3,
    )
    results: list[dict[str, float | int]] = []
    for prn in prns:
        code = _sampled_code(prn, sample_rate_hz, samples_per_ms)
        correlation = np.fft.ifft(
            spectra * np.conj(np.fft.fft(code))[None, None, None, :],
            axis=3,
        )
        # Same code/carrier hypothesis drives every spatial output.  Combine
        # squared magnitudes across both time dwells and independent spatial
        # dimensions; no desired steering phase is needed for acquisition.
        noncoherent = np.sum(np.abs(correlation) ** 2, axis=(0, 1))
        flat_peak = int(np.argmax(noncoherent))
        doppler_index, code_index = np.unravel_index(flat_peak, noncoherent.shape)
        peak = float(noncoherent[doppler_index, code_index])
        median = float(np.median(noncoherent))
        mean = float(np.mean(noncoherent))
        results.append(
            {
                "prn": int(prn),
                "doppler_hz": float(dopplers[doppler_index]),
                "code_phase_samples": int(code_index),
                "peak_to_median_db": float(
                    10.0 * np.log10(max(peak, 1e-30) / max(median, 1e-30))
                ),
                "peak_to_mean_db": float(
                    10.0 * np.log10(max(peak, 1e-30) / max(mean, 1e-30))
                ),
            }
        )
    return sorted(results, key=lambda row: float(row["peak_to_median_db"]), reverse=True)


def _sfap_power_inversion_streams(
    corrected: np.ndarray,
    *,
    sample_rate_hz: float,
    fft_size: int = 512,
    overlap_fraction: float = 0.25,
    snapshot_count: int = 100,
    diagonal_loading_rel: float = 1e-6,
    diagonal_loading_abs: float = 0.0,
    label: str,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Return blind per-bin SFAP power-inversion outputs.

    This follows the pre-correlation SFAP structure: window/DFT, estimate one
    spatial covariance matrix per frequency bin, diagonally load it, solve the
    minimum-output-power weight with a one-hot reference-element constraint,
    then overlap/IDFT.  Four reference constraints are retained as separate
    acquisition candidates; their correlation metrics are scale invariant.
    """

    channel_count = int(corrected.shape[0])
    overlap_samples = int(round(float(overlap_fraction) * int(fft_size)))
    if overlap_samples < 0 or overlap_samples >= int(fft_size):
        raise ValueError("overlap_fraction must produce 0 <= overlap < fft_size")

    _, _, spectra = stft(
        np.asarray(corrected, dtype=np.complex64),
        fs=float(sample_rate_hz),
        window="hann",
        nperseg=int(fft_size),
        noverlap=overlap_samples,
        return_onesided=False,
        boundary="zeros",
        padded=True,
        axis=-1,
    )
    measured_channels, bin_count, frame_count = spectra.shape
    if measured_channels != channel_count:
        raise RuntimeError("unexpected STFT channel dimension")
    output_spectra = np.empty(
        (channel_count, bin_count, frame_count), dtype=np.complex128
    )
    used_snapshots = min(max(1, int(snapshot_count)), frame_count)
    dominant_fractions: list[float] = []
    condition_numbers: list[float] = []
    loading_values: list[float] = []
    identity = np.eye(channel_count, dtype=np.complex128)
    for bin_index in range(bin_count):
        values = np.asarray(spectra[:, bin_index, :], dtype=np.complex128)
        training = values[:, :used_snapshots]
        covariance = training @ training.conj().T / float(used_snapshots)
        covariance = 0.5 * (covariance + covariance.conj().T)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        order = np.argsort(np.real(eigenvalues))[::-1]
        eigenvalues = np.maximum(np.real(eigenvalues[order]), 1e-30)
        fraction = float(eigenvalues[0] / np.sum(eigenvalues))
        dominant_fractions.append(fraction)
        loading = max(
            float(diagonal_loading_abs),
            float(diagonal_loading_rel)
            * float(np.real(np.trace(covariance)))
            / float(channel_count),
        )
        loaded = covariance + loading * identity
        loading_values.append(loading)
        condition_numbers.append(float(np.linalg.cond(loaded)))
        for reference_index in range(channel_count):
            constraint = identity[:, reference_index]
            try:
                solution = np.linalg.solve(loaded, constraint)
            except np.linalg.LinAlgError:
                solution = np.linalg.pinv(loaded) @ constraint
            denominator = complex(np.vdot(constraint, solution))
            if abs(denominator) <= 1e-20:
                weight = np.asarray(constraint, dtype=np.complex128)
            else:
                weight = np.asarray(solution / denominator, dtype=np.complex128)
            output_spectra[reference_index, bin_index] = weight.conj() @ values
    streams: dict[str, np.ndarray] = {}
    for reference_index in range(channel_count):
        _, output = istft(
            output_spectra[reference_index],
            fs=float(sample_rate_hz),
            window="hann",
            nperseg=int(fft_size),
            noverlap=overlap_samples,
            input_onesided=False,
            boundary=True,
        )
        streams[f"sfap_{label}_ref{reference_index}"] = np.asarray(
            output[: corrected.shape[1]], dtype=np.complex64
        )
    diagnostics = {
        "fft_size": int(fft_size),
        "overlap_fraction": float(overlap_fraction),
        "frame_count": int(frame_count),
        "snapshot_count": int(used_snapshots),
        "diagonal_loading_rel": float(diagonal_loading_rel),
        "diagonal_loading_abs": float(diagonal_loading_abs),
        "median_dominant_fraction": float(np.median(dominant_fractions)),
        "max_dominant_fraction": float(np.max(dominant_fractions)),
        "median_loaded_condition_number": float(np.median(condition_numbers)),
        "max_loaded_condition_number": float(np.max(condition_numbers)),
        "median_diagonal_loading": float(np.median(loading_values)),
    }
    return streams, diagnostics


def _stap_power_inversion_streams(
    corrected: np.ndarray,
    *,
    tap_count: int = 8,
    snapshot_count: int = 32_768,
    diagonal_loading_rel: float = 1e-3,
    diagonal_loading_abs: float = 0.0,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Return four blind space-time power-inversion reference outputs.

    Samples from every sensor and ``tap_count`` consecutive instants form one
    space-time snapshot.  One minimum-power solution is retained per physical
    reference sensor, constrained to unity on that sensor's zero-delay tap.
    Keeping the four outputs separate permits the same noncoherent acquisition
    combiner used for the spatial nullspace bank; it does not assume a desired
    steering vector during cold start.
    """

    values = np.asarray(corrected, dtype=np.complex64)
    if values.ndim != 2 or values.shape[0] < 2:
        raise ValueError("STAP expects a sensor-by-sample matrix")
    taps = int(tap_count)
    if taps < 2 or taps > values.shape[1]:
        raise ValueError("STAP tap count must be between 2 and sample count")
    channel_count, sample_count = values.shape
    windows = np.lib.stride_tricks.sliding_window_view(
        values,
        window_shape=taps,
        axis=1,
    )
    # Channel-major [current, previous, ...] ordering.  A zero-delay reference
    # constraint for sensor m is therefore at index m*taps.
    space_time = np.asarray(
        windows[:, :, ::-1].transpose(0, 2, 1).reshape(
            channel_count * taps,
            sample_count - taps + 1,
        ),
        dtype=np.complex128,
    )
    used_snapshots = min(max(1, int(snapshot_count)), space_time.shape[1])
    training = space_time[:, :used_snapshots]
    covariance = training @ training.conj().T / float(used_snapshots)
    covariance = 0.5 * (covariance + covariance.conj().T)
    dimension = int(covariance.shape[0])
    loading = max(
        float(diagonal_loading_abs),
        float(diagonal_loading_rel)
        * float(np.real(np.trace(covariance)))
        / float(dimension),
    )
    loaded = covariance + loading * np.eye(dimension, dtype=np.complex128)
    outputs = np.zeros((channel_count, sample_count), dtype=np.complex64)
    weight_norms: list[float] = []
    for reference_index in range(channel_count):
        constraint = np.zeros((dimension,), dtype=np.complex128)
        constraint[reference_index * taps] = 1.0
        try:
            solution = np.linalg.solve(loaded, constraint)
        except np.linalg.LinAlgError:
            solution = np.linalg.pinv(loaded) @ constraint
        denominator = complex(np.vdot(constraint, solution))
        if abs(denominator) <= 1e-20:
            weight = constraint
        else:
            weight = np.asarray(solution / denominator, dtype=np.complex128)
        weight_norms.append(float(np.linalg.norm(weight)))
        outputs[reference_index, taps - 1 :] = np.asarray(
            weight.conj() @ space_time,
            dtype=np.complex64,
        )
    diagnostics: dict[str, float | int] = {
        "tap_count": taps,
        "space_time_dimension": dimension,
        "snapshot_count": used_snapshots,
        "diagonal_loading_rel": float(diagonal_loading_rel),
        "diagonal_loading_abs": float(diagonal_loading_abs),
        "diagonal_loading": float(loading),
        "loaded_condition_number": float(np.linalg.cond(loaded)),
        "min_weight_norm": float(np.min(weight_norms)),
        "max_weight_norm": float(np.max(weight_norms)),
    }
    return outputs, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discard-chunks", type=int, default=4)
    parser.add_argument("--capture-chunks", type=int, default=4)
    parser.add_argument("--dwells", type=int, default=20)
    parser.add_argument("--prns", default="1-32")
    parser.add_argument("--nullspace-bank-only", action="store_true")
    parser.add_argument("--adaptive-bank-only", action="store_true")
    parser.add_argument("--save-iq", type=Path)
    parser.add_argument("--input-iq", type=Path)
    parser.add_argument("--stap-taps", type=int, default=8)
    args = parser.parse_args()

    cfg = build_runtime_config()
    captured: list[np.ndarray] = []
    states: list[str] = []
    if args.input_iq is not None:
        saved = np.load(args.input_iq)
        corrected = np.asarray(saved["corrected"], dtype=np.complex64)
        iq = np.asarray(saved.get("iq", corrected), dtype=np.complex64)
        states.append("offline_saved_iq")
    else:
        device = UsrpRxDevice(cfg)
        try:
            for _ in range(max(0, args.discard_chunks)):
                result = device.recv_chunk()
                states.append(str(result.state))
            for _ in range(max(1, args.capture_chunks)):
                result = device.recv_chunk()
                states.append(str(result.state))
                if result.got_samples > 0:
                    captured.append(np.asarray(result.chunk, dtype=np.complex64))
        finally:
            device.stop()
        if not captured:
            raise RuntimeError(f"USRP capture returned no samples; states={states}")
        iq = np.concatenate(captured, axis=1)
        correction = np.asarray(
            cfg.phase_correction_vector
            if cfg.phase_correction_vector is not None
            else np.ones((iq.shape[0],), dtype=np.complex128),
            dtype=np.complex128,
        )
        corrected = np.asarray(correction[:, None] * iq, dtype=np.complex64)
        if args.save_iq is not None:
            args.save_iq.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                args.save_iq,
                iq=np.asarray(iq, dtype=np.complex64),
                corrected=corrected,
                sample_rate_hz=float(cfg.sample_rate),
                center_frequency_hz=float(cfg.center_freq_hz),
            )
    evidence = cold_start_jammer_evidence(
        corrected,
        min_dominant_fraction=float(cfg.lcmv_cold_start_min_dominant_fraction),
        min_eigen_gap_db=float(cfg.lcmv_cold_start_min_eigen_gap_db),
        min_strongest_bin_fraction=float(
            cfg.lcmv_cold_start_min_spectral_concentration
        ),
        min_peak_over_median_db=float(
            cfg.lcmv_cold_start_min_peak_over_median_db
        ),
        strongest_bin_fraction=float(cfg.lcmv_cold_start_spectral_top_fraction),
    )
    selected_vectors, secondary_gaps_db = selected_interference_subspace(
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
    rank1_rows = jammer_nullspace_acquisition_rows(
        evidence.dominant_vector,
        source_count=4,
    )
    selected_rows = jammer_nullspace_acquisition_rows(
        selected_vectors,
        source_count=4,
    )
    rank1_basis_rows = _orthogonal_nullspace_rows(evidence.dominant_vector)
    selected_basis_rows = _orthogonal_nullspace_rows(selected_vectors)
    bank_streams = {
        "raw_channels_noncoherent": corrected,
        "rank1_nullspace_noncoherent": np.asarray(
            np.conj(rank1_basis_rows) @ corrected, dtype=np.complex64
        ),
        f"selected_rank{selected_vectors.shape[1]}_nullspace_noncoherent": np.asarray(
            np.conj(selected_basis_rows) @ corrected, dtype=np.complex64
        ),
    }
    prn_text = str(args.prns).strip()
    if prn_text == "1-32":
        prns = tuple(range(1, 33))
    else:
        prns = tuple(int(value) for value in prn_text.split(",") if value.strip())
    bank_acquisition = {
        name: _search_stream_bank(
            values,
            sample_rate_hz=float(cfg.sample_rate),
            prns=prns,
            dwell_count=int(args.dwells),
        )
        for name, values in bank_streams.items()
    }
    if args.adaptive_bank_only:
        stap_streams, stap_diagnostics = _stap_power_inversion_streams(
            corrected,
            tap_count=int(args.stap_taps),
            diagonal_loading_rel=float(cfg.lcmv_covariance_diagonal_loading_rel),
            diagonal_loading_abs=float(cfg.lcmv_covariance_diagonal_loading_abs),
        )
        sfap_streams_by_name, sfap_diagnostics = _sfap_power_inversion_streams(
            corrected,
            sample_rate_hz=float(cfg.sample_rate),
            diagonal_loading_rel=float(cfg.lcmv_covariance_diagonal_loading_rel),
            diagonal_loading_abs=float(cfg.lcmv_covariance_diagonal_loading_abs),
            label="runtime_dl",
        )
        sfap_streams = np.vstack(
            [sfap_streams_by_name[name] for name in sorted(sfap_streams_by_name)]
        )
        adaptive_banks = {
            **bank_streams,
            f"stap_{int(args.stap_taps)}tap_noncoherent": stap_streams,
            "sfap_512bin_noncoherent": sfap_streams,
        }
        adaptive_acquisition = {
            name: _search_stream_bank(
                values,
                sample_rate_hz=float(cfg.sample_rate),
                prns=prns,
                dwell_count=int(args.dwells),
            )
            for name, values in adaptive_banks.items()
        }
        payload = {
            "label": str(args.label),
            "captured_utc_unix_s": time.time(),
            "input_iq": str(args.input_iq) if args.input_iq is not None else None,
            "saved_iq": str(args.save_iq) if args.save_iq is not None else None,
            "sample_rate_hz": float(cfg.sample_rate),
            "center_frequency_hz": float(cfg.center_freq_hz),
            "sample_count": int(iq.shape[1]),
            "states": states,
            "cold_start_evidence": {
                "detected": bool(evidence.detected),
                "dominant_fraction": float(evidence.dominant_fraction),
                "dominant_over_second_db": float(evidence.dominant_over_second_db),
                "dominant_peak_offset_hz": float(
                    evidence.peak_normalized_frequency * cfg.sample_rate
                ),
                "eigenvalues": [float(value) for value in evidence.eigenvalues],
                "selected_rank": int(selected_vectors.shape[1]),
            },
            "bank_rms": {
                name: [
                    float(np.sqrt(np.mean(np.abs(stream) ** 2)))
                    for stream in values
                ]
                for name, values in adaptive_banks.items()
            },
            "adaptive_diagnostics": {
                "stap": stap_diagnostics,
                "sfap": sfap_diagnostics,
            },
            "noncoherent_bank_acquisition": adaptive_acquisition,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload))
        return 0
    if args.nullspace_bank_only:
        covariance = np.asarray(evidence.covariance, dtype=np.complex128)
        payload: dict[str, object] = {
            "label": str(args.label),
            "captured_utc_unix_s": time.time(),
            "sample_rate_hz": float(cfg.sample_rate),
            "center_frequency_hz": float(cfg.center_freq_hz),
            "sample_count": int(iq.shape[1]),
            "states": states,
            "cold_start_evidence": {
                "detected": bool(evidence.detected),
                "dominant_fraction": float(evidence.dominant_fraction),
                "dominant_over_second_db": float(evidence.dominant_over_second_db),
                "dominant_peak_offset_hz": float(
                    evidence.peak_normalized_frequency * cfg.sample_rate
                ),
                "eigenvalues": [float(value) for value in evidence.eigenvalues],
                "selected_rank": int(selected_vectors.shape[1]),
                "secondary_eigen_gaps_db": [
                    float(value) for value in secondary_gaps_db
                ],
            },
            "bank_diagnostics": {
                "raw_spatial_dimension": int(corrected.shape[0]),
                "rank1_nullspace_dimension": int(rank1_basis_rows.shape[0]),
                "selected_nullspace_dimension": int(selected_basis_rows.shape[0]),
                "rank1_max_null_residual_abs": float(
                    np.max(
                        np.abs(
                            np.conj(rank1_basis_rows)
                            @ np.asarray(evidence.dominant_vector).reshape(-1, 1)
                        )
                    )
                ),
                "selected_max_null_residual_abs": float(
                    np.max(np.abs(np.conj(selected_basis_rows) @ selected_vectors))
                ),
                "bank_rms": {
                    name: [
                        float(np.sqrt(np.mean(np.abs(stream) ** 2)))
                        for stream in values
                    ]
                    for name, values in bank_streams.items()
                },
            },
            "noncoherent_bank_acquisition": bank_acquisition,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload))
        return 0
    spatial_streams = {
        "uniform_sum": np.sum(corrected, axis=0),
        **{
            f"rank1_null_{index}": np.asarray(
                np.conj(row) @ corrected, dtype=np.complex64
            )
            for index, row in enumerate(rank1_rows)
        },
        **{
            f"selected_rank{selected_vectors.shape[1]}_null_{index}": np.asarray(
                np.conj(row) @ corrected, dtype=np.complex64
            )
            for index, row in enumerate(selected_rows)
        },
    }
    sfap_paper_streams, sfap_paper_diagnostics = _sfap_power_inversion_streams(
        corrected,
        sample_rate_hz=float(cfg.sample_rate),
        diagonal_loading_rel=1e-6,
        label="dl1e6",
    )
    sfap_runtime_streams, sfap_runtime_diagnostics = _sfap_power_inversion_streams(
        corrected,
        sample_rate_hz=float(cfg.sample_rate),
        diagonal_loading_rel=float(cfg.lcmv_covariance_diagonal_loading_rel),
        diagonal_loading_abs=float(cfg.lcmv_covariance_diagonal_loading_abs),
        label="runtime_dl",
    )
    sfap_streams = {**sfap_paper_streams, **sfap_runtime_streams}
    notch = StatefulComplexBandstop(
        sample_rate_hz=float(cfg.sample_rate),
        bandwidth_hz=float(cfg.lcmv_cold_start_frequency_notch_bandwidth_hz),
        num_taps=int(cfg.lcmv_cold_start_frequency_notch_fir_taps),
    )
    notch.tune(float(evidence.peak_normalized_frequency * cfg.sample_rate))
    notched_matrix = notch.process(
        np.vstack(tuple({**spatial_streams, **sfap_streams}.values()))
    )
    all_processed_streams = {**spatial_streams, **sfap_streams}
    notched_streams = {
        f"{name}_notched": notched_matrix[index]
        for index, name in enumerate(all_processed_streams)
    }
    streams = {
        **{f"raw_ch{index}": iq[index] for index in range(iq.shape[0])},
        **spatial_streams,
        **sfap_streams,
        **notched_streams,
    }
    covariance = np.asarray(evidence.covariance, dtype=np.complex128)
    uniform = np.ones((corrected.shape[0],), dtype=np.complex128)
    uniform_power = float(np.real(np.vdot(uniform, covariance @ uniform)))
    payload: dict[str, object] = {
        "label": str(args.label),
        "captured_utc_unix_s": time.time(),
        "sample_rate_hz": float(cfg.sample_rate),
        "center_frequency_hz": float(cfg.center_freq_hz),
        "sample_count": int(iq.shape[1]),
        "states": states,
        "cold_start_evidence": {
            "detected": bool(evidence.detected),
            "dominant_fraction": float(evidence.dominant_fraction),
            "dominant_over_second_db": float(evidence.dominant_over_second_db),
            "dominant_spectral_concentration": float(
                evidence.strongest_bin_fraction
            ),
            "dominant_peak_over_median_db": float(evidence.peak_over_median_db),
            "dominant_peak_offset_hz": float(
                evidence.peak_normalized_frequency * cfg.sample_rate
            ),
            "secondary_spectral_concentration": float(
                evidence.secondary_strongest_bin_fraction
            ),
            "secondary_peak_over_median_db": float(
                evidence.secondary_peak_over_median_db
            ),
            "eigenvalues": [float(value) for value in evidence.eigenvalues],
            "selected_rank": int(selected_vectors.shape[1]),
            "secondary_eigen_gaps_db": [
                float(value) for value in secondary_gaps_db
            ],
        },
        "spatial_output_reduction_db": {
            name: float(
                10.0
                * np.log10(
                    max(uniform_power, 1e-30)
                    / max(
                        float(np.real(np.vdot(row, covariance @ row))),
                        1e-30,
                    )
                )
            )
            for name, row in {
                **{f"rank1_null_{index}": row for index, row in enumerate(rank1_rows)},
                **{
                    f"selected_rank{selected_vectors.shape[1]}_null_{index}": row
                    for index, row in enumerate(selected_rows)
                },
            }.items()
        },
        "sfap_diagnostics": {
            "paper_dl1e6": sfap_paper_diagnostics,
            "runtime_loading": sfap_runtime_diagnostics,
        },
        "stream_rms": {
            name: float(np.sqrt(np.mean(np.abs(values) ** 2)))
            for name, values in streams.items()
        },
        "acquisition": {
            name: _search_stream(
                values,
                sample_rate_hz=float(cfg.sample_rate),
                prns=prns,
                dwell_count=int(args.dwells),
            )
            for name, values in streams.items()
        },
        "noncoherent_bank_acquisition": bank_acquisition,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
