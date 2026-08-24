"""Stateful complex-baseband notch filtering for a detected narrowband tone."""

from __future__ import annotations

import numpy as np
from scipy.fft import fft, ifft, next_fast_len
from scipy.signal import lfilter
from scipy.signal import firwin


class StatefulComplexNotch:
    """Apply one narrow complex IIR notch while preserving chunk continuity.

    The zero is placed at the detected signed baseband frequency. A nearby pole
    controls notch width. Filter state is retained independently for every GNSS
    fanout row so processing a sequence of chunks is identical to processing one
    continuous stream.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        pole_radius: float = 0.995,
        order: int = 1,
    ) -> None:
        sample_rate = float(sample_rate_hz)
        radius = float(pole_radius)
        if not np.isfinite(sample_rate) or sample_rate <= 0.0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if not np.isfinite(radius) or not 0.0 < radius < 1.0:
            raise ValueError("pole_radius must be strictly between zero and one")
        filter_order = int(order)
        if filter_order < 1:
            raise ValueError("order must be at least one")
        self.sample_rate_hz = sample_rate
        self.pole_radius = radius
        self.order = filter_order
        self.frequency_offset_hz: float | None = None
        self._b = np.asarray([1.0, -1.0], dtype=np.complex128)
        self._a = np.asarray([1.0, -radius], dtype=np.complex128)
        self._zi: np.ndarray | None = None

    @property
    def active(self) -> bool:
        return self.frequency_offset_hz is not None

    def reset(self) -> None:
        self.frequency_offset_hz = None
        self._zi = None

    def tune(self, frequency_offset_hz: float) -> None:
        offset = float(frequency_offset_hz)
        if not np.isfinite(offset):
            raise ValueError("frequency_offset_hz must be finite")
        nyquist = 0.5 * self.sample_rate_hz
        if not -nyquist <= offset < nyquist:
            raise ValueError("frequency_offset_hz must lie inside the sampled band")
        if self.frequency_offset_hz == offset:
            return
        omega = 2.0 * np.pi * offset / self.sample_rate_hz
        zero = np.exp(1j * omega)
        # This scale gives exactly unity gain on the side opposite the notch;
        # far from the removed tone the passband remains essentially unchanged.
        scale = 0.5 * (1.0 + self.pole_radius)
        self._b = np.asarray(
            (scale**self.order)
            * np.poly(np.repeat(zero, self.order)),
            dtype=np.complex128,
        )
        self._a = np.asarray(
            np.poly(np.repeat(self.pole_radius * zero, self.order)),
            dtype=np.complex128,
        )
        self.frequency_offset_hz = offset
        # Retuning changes the IIR state definition. Start the new stable filter
        # from zero rather than mixing state from two different notch centers.
        self._zi = None

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples)
        if x.ndim not in {1, 2}:
            raise ValueError("samples must be a vector or row-major matrix")
        if x.shape[-1] == 0 or not self.active:
            return np.asarray(x, dtype=np.complex64)

        row_count = 1 if x.ndim == 1 else int(x.shape[0])
        expected_state_shape = (
            (self.order,)
            if x.ndim == 1
            else (row_count, self.order)
        )
        if self._zi is None or self._zi.shape != expected_state_shape:
            self._zi = np.zeros(expected_state_shape, dtype=np.complex128)
        filtered, final_state = lfilter(
            self._b,
            self._a,
            np.asarray(x, dtype=np.complex128),
            axis=-1,
            zi=self._zi,
        )
        self._zi = np.asarray(final_state, dtype=np.complex128)
        return np.asarray(filtered, dtype=np.complex64)


class StatefulComplexBandstop:
    """Streaming linear-phase FIR rejection band at one complex frequency."""

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        bandwidth_hz: float,
        num_taps: int = 257,
    ) -> None:
        sample_rate = float(sample_rate_hz)
        bandwidth = float(bandwidth_hz)
        taps = int(num_taps)
        if not np.isfinite(sample_rate) or sample_rate <= 0.0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if not np.isfinite(bandwidth) or not 0.0 < bandwidth < sample_rate:
            raise ValueError("bandwidth_hz must lie inside the sampled band")
        if taps < 3 or taps % 2 == 0:
            raise ValueError("num_taps must be an odd integer of at least three")
        self.sample_rate_hz = sample_rate
        self.bandwidth_hz = bandwidth
        self.num_taps = taps
        self.group_delay_samples = (taps - 1) // 2
        self.frequency_offset_hz: float | None = None
        self._coefficients = np.zeros((taps,), dtype=np.complex128)
        self._tail: np.ndarray | None = None
        self._cached_fft_size = 0
        self._cached_response = np.zeros((0,), dtype=np.complex128)

    @property
    def active(self) -> bool:
        return self.frequency_offset_hz is not None

    def reset(self) -> None:
        self.frequency_offset_hz = None
        self._tail = None
        self._cached_fft_size = 0
        self._cached_response = np.zeros((0,), dtype=np.complex128)

    def tune(self, frequency_offset_hz: float) -> None:
        offset = float(frequency_offset_hz)
        if not np.isfinite(offset):
            raise ValueError("frequency_offset_hz must be finite")
        nyquist = 0.5 * self.sample_rate_hz
        half_bandwidth = 0.5 * self.bandwidth_hz
        if not -nyquist <= offset < nyquist:
            raise ValueError("frequency_offset_hz must lie inside the sampled band")
        if offset - half_bandwidth < -nyquist or offset + half_bandwidth >= nyquist:
            raise ValueError("the rejection band must lie inside the sampled band")
        if self.frequency_offset_hz == offset:
            return

        index = np.arange(self.num_taps, dtype=np.float64)
        delay = float(self.group_delay_samples)
        lowpass = firwin(
            self.num_taps,
            half_bandwidth,
            fs=self.sample_rate_hz,
            window=("kaiser", 8.6),
        )
        rejected_band = lowpass * np.exp(
            2j
            * np.pi
            * offset
            / self.sample_rate_hz
            * (index - delay)
        )
        coefficients = -np.asarray(rejected_band, dtype=np.complex128)
        coefficients[self.group_delay_samples] += 1.0
        self._coefficients = coefficients
        self.frequency_offset_hz = offset
        self._tail = None
        self._cached_fft_size = 0
        self._cached_response = np.zeros((0,), dtype=np.complex128)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples)
        if x.ndim not in {1, 2}:
            raise ValueError("samples must be a vector or row-major matrix")
        if x.shape[-1] == 0 or not self.active:
            return np.asarray(x, dtype=np.complex64)

        was_vector = x.ndim == 1
        matrix = np.asarray(
            x[None, :] if was_vector else x,
            dtype=np.complex128,
        )
        row_count = int(matrix.shape[0])
        tail_count = self.num_taps - 1
        if self._tail is None or self._tail.shape != (row_count, tail_count):
            self._tail = np.zeros((row_count, tail_count), dtype=np.complex128)
        extended = np.concatenate((self._tail, matrix), axis=-1)
        fft_size = int(next_fast_len(int(extended.shape[-1])))
        if fft_size != self._cached_fft_size:
            self._cached_response = fft(self._coefficients, fft_size)
            self._cached_fft_size = fft_size
        circular = ifft(
            fft(extended, fft_size, axis=-1) * self._cached_response,
            axis=-1,
        )
        sample_count = int(matrix.shape[-1])
        filtered = circular[:, tail_count : tail_count + sample_count]
        self._tail = np.array(extended[:, -tail_count:], copy=True)
        result = filtered[0] if was_vector else filtered
        return np.asarray(result, dtype=np.complex64)


__all__ = ["StatefulComplexBandstop", "StatefulComplexNotch"]
