#!/usr/bin/env python3
"""One-shot USRP spatial source-count diagnostic for the X300 realtime profile."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "error")

from antijamming.config.loader import default_stream_config
from antijamming.config.schemas.runtime import DEFAULT_RUNTIME_CONFIG_PATH
from antijamming.dsp.doa.music import music_spectrum, source_count_diagnostics
from antijamming.dsp.models import internal_angle_to_operator_bearing_deg
from antijamming.dsp.phase.alignment import (
    apply_phase_calibration,
    load_calibration_correction_selection,
    phase_offsets_deg,
)
from antijamming.radio.usrp import UsrpRxDevice


def db10(value: np.ndarray | float, floor: float = 1e-30) -> np.ndarray | float:
    return 10.0 * np.log10(np.maximum(np.asarray(value), floor))


def covariance_matrix(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.complex128)
    return (x @ x.conj().T) / max(int(x.shape[1]), 1)


def eigen_report(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    evals, evecs = np.linalg.eigh(np.asarray(r, dtype=np.complex128))
    order = np.argsort(evals)[::-1]
    return np.asarray(evals[order], dtype=np.float64), np.asarray(evecs[:, order])


def circular_sep_deg(a: float, b: float) -> float:
    delta = abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)
    return float(delta)


def top_music_peaks(
    scan_angles_deg: np.ndarray,
    spectrum: np.ndarray,
    max_peaks: int,
    min_separation_deg: float,
) -> list[tuple[float, float, float]]:
    scan = np.asarray(scan_angles_deg, dtype=np.float64)
    spec = np.asarray(spectrum, dtype=np.float64)
    order = np.argsort(spec)[::-1]
    peaks: list[tuple[float, float, float]] = []
    for idx in order:
        internal = float(scan[int(idx)])
        if any(circular_sep_deg(internal, existing[0]) < min_separation_deg for existing in peaks):
            continue
        display = internal_angle_to_operator_bearing_deg(internal)
        peaks.append((internal, display, float(spec[int(idx)])))
        if len(peaks) >= max_peaks:
            break
    return peaks


def receive_snapshot(device: UsrpRxDevice, chunks: int) -> tuple[np.ndarray, Counter[str], float]:
    buffers: list[np.ndarray] = []
    states: Counter[str] = Counter()
    start = time.monotonic()
    attempts = 0
    max_attempts = max(int(chunks) + 10, 12)
    while len(buffers) < int(chunks) and attempts < max_attempts:
        attempts += 1
        try:
            result = device.recv_chunk()
        except Exception:
            if attempts == 1:
                device.restart_stream()
                time.sleep(0.2)
                continue
            raise
        chunk, state = result
        states[str(state)] += 1
        if int(getattr(result, "got_samples", 0) or 0) > 0 and chunk.shape[1] > 0:
            buffers.append(np.asarray(chunk, dtype=np.complex64))
    elapsed = time.monotonic() - start
    if not buffers:
        raise RuntimeError(f"USRP returned no samples after {attempts} recv attempts")
    return np.concatenate(buffers, axis=1), states, elapsed


def format_complex_vector(values: np.ndarray) -> str:
    parts: list[str] = []
    for idx, value in enumerate(np.asarray(values, dtype=np.complex128).reshape(-1)):
        parts.append(
            f"s{idx}:abs={abs(value):.3f},phase={np.degrees(np.angle(value)):+.1f}deg"
        )
    return " ".join(parts)


def format_float_vector(values: object) -> str:
    return ",".join(f"{float(value):.2f}" for value in np.asarray(values).reshape(-1))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a short coherent USRP snapshot and estimate spatial RF source count."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_PATH,
        help="Runtime JSON profile to use.",
    )
    parser.add_argument(
        "--chunks",
        type=int,
        default=64,
        help="Number of configured samples_per_chunk buffers to capture.",
    )
    parser.add_argument(
        "--music-peaks",
        type=int,
        default=4,
        help="Number of separated MUSIC peaks to print per source assumption.",
    )
    parser.add_argument(
        "--peak-separation-deg",
        type=float,
        default=8.0,
        help="Minimum angular separation between printed MUSIC peaks.",
    )
    args = parser.parse_args()

    cfg = default_stream_config(args.config)
    correction = None
    calibration_metadata: dict[str, object] = {}
    cal_file = cfg.phase_calibration_file
    if cal_file is not None:
        selection = load_calibration_correction_selection(
            cal_file,
            mode=str(cfg.calibration_correction_mode),
            expected_channel_count=len(cfg.channels),
        )
        correction = selection.vector
        calibration_metadata = selection.metadata(
            expected_channel_count=len(cfg.channels)
        )

    print("USRP spatial source-count snapshot")
    print(f"config={Path(args.config).resolve()}")
    print(
        "rf="
        f"addr={cfg.usrp_addr} channels={list(cfg.channels)} "
        f"rate={cfg.sample_rate/1e6:.3f}Msps freq={cfg.center_freq_hz/1e6:.3f}MHz "
        f"gain={cfg.gain_db:.1f}dB bw={cfg.usrp_rx_bandwidth_hz/1e6:.3f}MHz"
    )
    print(
        "array="
        f"spacing_m={cfg.array_spacing_m:.6f} "
        f"design_freq={cfg.array_design_freq_hz/1e6:.3f}MHz steering_freq={cfg.center_freq_hz/1e6:.3f}MHz"
    )
    print(
        "calibration="
        f"{cal_file if cal_file is not None else 'none'} "
        f"configured_mode={getattr(cfg, 'calibration_correction_mode', 'complex_gain')} "
        f"applied_mode={calibration_metadata.get('calibration_correction_mode_applied', 'none')} "
        f"fallback_used={calibration_metadata.get('fallback_used', False)}"
    )
    if calibration_metadata:
        print(
            "calibration_vector="
            f"magnitudes=[{format_float_vector(calibration_metadata.get('applied_correction_magnitudes', []))}] "
            f"phases_deg=[{format_float_vector(calibration_metadata.get('applied_correction_phases_deg', []))}]"
        )

    device: UsrpRxDevice | None = None
    try:
        device = UsrpRxDevice(cfg)
        raw, states, elapsed = receive_snapshot(device, chunks=max(1, int(args.chunks)))
    finally:
        if device is not None:
            try:
                device.stop()
            except Exception:
                pass

    corrected = apply_phase_calibration(raw, correction_vector=correction)
    raw_powers = np.mean(np.abs(raw) ** 2, axis=1)
    corrected_powers = np.mean(np.abs(corrected) ** 2, axis=1)
    raw_phase = phase_offsets_deg(raw)
    corrected_phase = phase_offsets_deg(corrected)
    r = covariance_matrix(corrected)
    evals, evecs = eigen_report(r)
    sample_count = int(corrected.shape[1])
    eig_db = np.asarray(db10(evals), dtype=np.float64)
    eig_rel_db = eig_db - float(eig_db[0])
    gaps = evals[:-1] / np.maximum(evals[1:], 1e-30)
    gap_sources = int(np.argmax(gaps) + 1) if gaps.size else 0
    p = evals / max(float(np.sum(evals)), 1e-30)
    effective_rank = float(np.exp(-np.sum(p * np.log(np.maximum(p, 1e-30)))))
    noise_tail = source_count_diagnostics(corrected, noise_tail_sources=1)
    dominant = np.asarray(evecs[:, 0], dtype=np.complex128)
    if dominant.size:
        dominant = dominant * np.exp(-1j * np.angle(dominant[0]))

    print(
        "capture="
        f"chunks={args.chunks} samples_per_channel={sample_count} elapsed_s={elapsed:.3f} "
        f"recv_states={dict(states)}"
    )
    print("channel_power_raw_db=")
    for stream_idx, usrp_ch in enumerate(cfg.channels):
        antenna = cfg.rx_antennas_by_channel[int(usrp_ch)]
        print(
            f"  stream{stream_idx}->ch{usrp_ch}({antenna}) "
            f"raw={float(db10(raw_powers[stream_idx])):.2f} "
            f"cal={float(db10(corrected_powers[stream_idx])):.2f} "
            f"raw_phase={raw_phase[stream_idx]:+.2f}deg "
            f"cal_phase={corrected_phase[stream_idx]:+.2f}deg"
        )
    strongest_idx = int(np.argmax(raw_powers))
    print(
        "strongest_channel="
        f"stream{strongest_idx}->ch{cfg.channels[strongest_idx]} "
        f"power_raw_db={float(db10(raw_powers[strongest_idx])):.2f}"
    )
    print("covariance_eigenvalues=")
    for idx, value in enumerate(evals):
        gap_text = f" gap_to_next={10.0*np.log10(gaps[idx]):.2f}dB" if idx < gaps.size else ""
        print(
            f"  eig{idx + 1}={value:.6e} abs_db={eig_db[idx]:.2f} "
            f"rel_db={eig_rel_db[idx]:+.2f}{gap_text}"
        )
    print(
        "source_count_estimates="
        f"largest_eigen_gap={gap_sources} "
        f"effective_rank={effective_rank:.2f}"
    )
    print(
        "white_noise_tail_assuming_one_source="
        f"tail_count={int(noise_tail.get('noise_tail_count', 0))} "
        f"tail_rel_db=[{format_float_vector(noise_tail.get('noise_tail_eigenvalues_rel_db', []))}] "
        f"spread_db={float(noise_tail.get('noise_tail_spread_db', 0.0)):.2f} "
        f"flatness_db={float(noise_tail.get('noise_tail_flatness_db', 0.0)):.2f} "
        f"white_like={int(bool(noise_tail.get('noise_tail_white_like', False)))} "
        f"thresholds=spread<={float(noise_tail.get('noise_tail_spread_threshold_db', 0.0)):.1f}dB,"
        f"flatness<={float(noise_tail.get('noise_tail_flatness_threshold_db', 0.0)):.1f}dB"
    )
    print(f"dominant_eigenvector={format_complex_vector(dominant)}")
    scan = cfg.angle_scan_spec().values()
    max_sources = max(1, min(len(cfg.channels) - 1, 3))
    for n_sources in range(1, max_sources + 1):
        spec = music_spectrum(
            x=corrected,
            rf_freq_hz=float(cfg.center_freq_hz),
            scan_angles_deg=scan,
            array_spacing_m=float(cfg.array_spacing_m),
            n_sources=n_sources,
            normalize=True,
        )
        peaks = top_music_peaks(
            scan,
            spec,
            max_peaks=max(1, int(args.music_peaks)),
            min_separation_deg=max(0.0, float(args.peak_separation_deg)),
        )
        print(f"music_peaks_assuming_{n_sources}_source(s)=")
        for rank, (internal, display, score) in enumerate(peaks, start=1):
            print(
                f"  peak{rank}: internal={internal:.2f}deg "
                f"display_clockwise={display:.2f}deg score={score:.4f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
