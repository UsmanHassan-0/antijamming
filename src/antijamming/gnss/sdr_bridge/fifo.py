"""FIFO writer setup, pipe sizing, and FIFO cleanup helpers."""

from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path

import numpy as np


def complex64_contiguous_vector(samples: np.ndarray) -> np.ndarray:
    """Return a flat complex64 C-contiguous vector for GNSS-SDR FIFO bytes."""
    array = np.asarray(samples)
    if array.dtype == np.complex64 and array.flags.c_contiguous:
        return array.reshape(-1)
    return np.ascontiguousarray(array, dtype=np.complex64).reshape(-1)


class FifoMixin:
    def _open_fifo_writer(self, timeout_s: float) -> int:
        deadline = time.monotonic() + timeout_s
        while True:
            if self._proc is None:
                raise RuntimeError("GNSS-SDR process did not start.")
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"GNSS-SDR exited early with code {self._proc.returncode}. See gnss_sdr.log."
                )
            try:
                fd = os.open(self._fifo_path, os.O_WRONLY | os.O_NONBLOCK)
                self._configure_pipe(fd)
                os.set_blocking(fd, True)
                return fd
            except OSError as exc:
                if exc.errno == errno.ENXIO and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                raise RuntimeError(f"Could not open GNSS-SDR FIFO writer: {exc}") from exc

    def _configure_pipe(self, fd: int) -> None:
        desired_bytes = self._desired_fifo_pipe_size_bytes()
        try:
            fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, desired_bytes)
        except OSError as exc:
            self._log.warning(
                "Failed to raise GNSS FIFO pipe size to %d bytes: %s",
                desired_bytes,
                exc,
            )
        try:
            self._pipe_size_bytes = int(fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ))
        except OSError:
            self._pipe_size_bytes = None

    def _desired_fifo_pipe_size_bytes(self) -> int:
        chunk_bytes = max(1, int(self._cfg.samples_per_chunk)) * np.dtype(np.complex64).itemsize
        # Keep enough pipe space for brief GNSS-SDR reader stalls before the
        # backend's raw queue has to absorb the full burst.
        desired_bytes = max(262_144, chunk_bytes * 16)
        pipe_max = self._linux_pipe_max_size_bytes()
        if pipe_max is None:
            return desired_bytes
        return min(desired_bytes, pipe_max)

    def _linux_pipe_max_size_bytes(self) -> int | None:
        path = Path("/proc/sys/fs/pipe-max-size")
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _cleanup_fifo(self) -> None:
        try:
            if self._fifo_path.exists() or self._fifo_path.is_symlink():
                self._fifo_path.unlink()
        except OSError:
            pass
