from __future__ import annotations

import numpy as np
import pytest

from antijamming.dsp.frequency_notch import (
    StatefulComplexBandstop,
    StatefulComplexNotch,
)


def _tone_power(samples: np.ndarray, normalized_frequency: float) -> float:
    x = np.asarray(samples, dtype=np.complex128)
    index = np.arange(x.shape[-1], dtype=np.float64)
    mixer = np.exp(-2j * np.pi * normalized_frequency * index)
    line = np.mean(x * mixer, axis=-1)
    return float(np.mean(np.abs(line) ** 2))


def test_complex_notch_removes_detected_tone_and_preserves_far_signal() -> None:
    sample_rate_hz = 4_000_000.0
    sample_count = 131_072
    index = np.arange(sample_count, dtype=np.float64)
    notch_normalized = -0.093017578125
    pass_normalized = 0.125
    tone = np.exp(2j * np.pi * notch_normalized * index)
    desired = 0.2 * np.exp(2j * np.pi * pass_normalized * index)
    source = np.vstack([tone + desired, 0.7 * tone + desired]).astype(np.complex64)

    notch = StatefulComplexNotch(
        sample_rate_hz=sample_rate_hz,
        pole_radius=0.995,
    )
    notch.tune(notch_normalized * sample_rate_hz)
    output = notch.process(source)
    settled_source = source[:, 8192:]
    settled_output = output[:, 8192:]

    tone_reduction_db = 10.0 * np.log10(
        _tone_power(settled_source, notch_normalized)
        / _tone_power(settled_output, notch_normalized)
    )
    passband_change_db = 10.0 * np.log10(
        _tone_power(settled_output, pass_normalized)
        / _tone_power(settled_source, pass_normalized)
    )
    assert tone_reduction_db > 80.0
    assert abs(passband_change_db) < 0.1


def test_chunked_filtering_matches_one_continuous_filter() -> None:
    rng = np.random.default_rng(20260823)
    source = (
        rng.standard_normal((3, 65536))
        + 1j * rng.standard_normal((3, 65536))
    ).astype(np.complex64)
    one_shot = StatefulComplexNotch(sample_rate_hz=4e6, pole_radius=0.995)
    chunked = StatefulComplexNotch(sample_rate_hz=4e6, pole_radius=0.995)
    one_shot.tune(-372_070.3125)
    chunked.tune(-372_070.3125)

    expected = one_shot.process(source)
    actual = np.concatenate(
        [
            chunked.process(source[:, :16384]),
            chunked.process(source[:, 16384:49152]),
            chunked.process(source[:, 49152:]),
        ],
        axis=1,
    )

    assert actual == pytest.approx(expected, abs=2e-6)


@pytest.mark.parametrize("edge_hz", [-265_000.0, 265_000.0])
def test_fourth_order_notch_suppresses_measured_band_edges(edge_hz: float) -> None:
    sample_rate_hz = 4_000_000.0
    center_hz = -372_070.3125
    sample_count = 131_072
    index = np.arange(sample_count, dtype=np.float64)
    frequency_hz = center_hz + edge_hz
    source = np.exp(
        2j * np.pi * frequency_hz / sample_rate_hz * index
    ).astype(np.complex64)
    notch = StatefulComplexNotch(
        sample_rate_hz=sample_rate_hz,
        pole_radius=0.584,
        order=4,
    )
    notch.tune(center_hz)
    output = notch.process(source)
    source_power = float(np.mean(np.abs(source[8192:]) ** 2))
    output_power = float(np.mean(np.abs(output[8192:]) ** 2))

    assert 10.0 * np.log10(source_power / output_power) > 11.0


def test_reset_bypasses_until_retuned() -> None:
    source = np.ones((2048,), dtype=np.complex64)
    notch = StatefulComplexNotch(sample_rate_hz=4e6, pole_radius=0.995)
    notch.tune(100_000.0)
    notch.process(source)
    notch.reset()

    assert notch.process(source) == pytest.approx(source)


@pytest.mark.parametrize("edge_hz", [-265_000.0, 265_000.0])
def test_fir_bandstop_rejects_measured_530khz_band(edge_hz: float) -> None:
    sample_rate_hz = 4_000_000.0
    center_hz = -372_070.3125
    sample_count = 131_072
    index = np.arange(sample_count, dtype=np.float64)
    frequency_hz = center_hz + edge_hz
    source = np.exp(
        2j * np.pi * frequency_hz / sample_rate_hz * index
    ).astype(np.complex64)
    bandstop = StatefulComplexBandstop(
        sample_rate_hz=sample_rate_hz,
        bandwidth_hz=600_000.0,
        num_taps=257,
    )
    bandstop.tune(center_hz)
    output = bandstop.process(source)

    source_power = float(np.mean(np.abs(source[8192:]) ** 2))
    output_power = float(np.mean(np.abs(output[8192:]) ** 2))
    assert 10.0 * np.log10(source_power / output_power) > 45.0


def test_fir_bandstop_chunked_output_matches_continuous_output() -> None:
    rng = np.random.default_rng(20260824)
    source = (
        rng.standard_normal((4, 65536))
        + 1j * rng.standard_normal((4, 65536))
    ).astype(np.complex64)
    one_shot = StatefulComplexBandstop(
        sample_rate_hz=4e6,
        bandwidth_hz=600e3,
        num_taps=257,
    )
    chunked = StatefulComplexBandstop(
        sample_rate_hz=4e6,
        bandwidth_hz=600e3,
        num_taps=257,
    )
    one_shot.tune(-372_070.3125)
    chunked.tune(-372_070.3125)

    expected = one_shot.process(source)
    actual = np.concatenate(
        [
            chunked.process(source[:, :16384]),
            chunked.process(source[:, 16384:49152]),
            chunked.process(source[:, 49152:]),
        ],
        axis=1,
    )

    assert actual == pytest.approx(expected, abs=3e-6)
