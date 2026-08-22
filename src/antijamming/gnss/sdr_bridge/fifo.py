"""FIFO writer setup, pipe sizing, and FIFO cleanup helpers."""

from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path

import numpy as np


_FIFO_STARTUP_PROGRESS_INTERVAL_S = 10.0
_GNSS_STARTUP_CONSOLE_ENV = "ANTIJAM_GNSS_STARTUP_CONSOLE"
# One 32,768-sample complex64 chunk is 262,144 bytes, while startup raises
# every FIFO pipe to 1 MiB on the deployment host.  Writing one full runtime
# chunk per source cuts the ten-source fanout from 80 syscalls/chunk to 10
# without changing sample order or dropping data.  Smaller chunks naturally
# use their full size in ``GnssSdrBridge.write``.
PER_SOURCE_FIFO_STRIPE_SAMPLES = 32_768


def complex64_contiguous_vector(samples: np.ndarray) -> np.ndarray:
    """Return a flat complex64 C-contiguous vector for GNSS-SDR FIFO bytes."""
    array = np.asarray(samples)
    if array.dtype == np.complex64 and array.flags.c_contiguous:
        return array.reshape(-1)
    return np.ascontiguousarray(array, dtype=np.complex64).reshape(-1)


class FifoMixin:
    def _report_startup(self, message: str, *args: object) -> None:
        self._log.info(message, *args)
        console_value = os.environ.get(_GNSS_STARTUP_CONSOLE_ENV, "0").strip().lower()
        if console_value not in {"", "0", "false", "no", "off"}:
            rendered = message % args if args else message
            try:
                print(f"[run_realtime] {rendered}", flush=True)
            except OSError:
                # Terminal output is diagnostic only; a closed launcher pipe
                # must never break GNSS-SDR startup.
                pass

    def _open_fifo_writer(
        self,
        timeout_s: float | None,
        fifo_path: Path | None = None,
    ) -> int:
        selected_path = Path(fifo_path) if fifo_path is not None else self._fifo_path
        started_at = time.monotonic()
        timeout_value = None
        if timeout_s is not None and float(timeout_s) > 0.0:
            timeout_value = float(timeout_s)
        deadline = None if timeout_value is None else started_at + timeout_value
        next_progress_log_at = started_at + _FIFO_STARTUP_PROGRESS_INTERVAL_S

        while True:
            if self._proc is None:
                raise RuntimeError("GNSS-SDR process did not start.")
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"GNSS-SDR exited early with code {self._proc.returncode}. See gnss_sdr.log."
                )
            try:
                fd = os.open(selected_path, os.O_WRONLY | os.O_NONBLOCK)
                self._configure_pipe(fd)
                os.set_blocking(fd, True)
                elapsed_s = time.monotonic() - started_at
                if elapsed_s >= _FIFO_STARTUP_PROGRESS_INTERVAL_S:
                    self._report_startup(
                        "GNSS-SDR ready after %.1fs. FFTW finished measuring plans "
                        "for %.3f Msps and refreshed %s; later starts at this rate "
                        "should be fast.",
                        elapsed_s,
                        float(self._cfg.sample_rate) / 1e6,
                        str(Path.home() / ".gr_fftw_wisdom"),
                    )
                else:
                    self._report_startup(
                        "GNSS-SDR ready in %.3fs; cached FFTW plans already covered "
                        "%.3f Msps.",
                        elapsed_s,
                        float(self._cfg.sample_rate) / 1e6,
                    )
                return fd
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise RuntimeError(
                        f"Could not open GNSS-SDR FIFO writer {selected_path}: {exc}"
                    ) from exc

                now = time.monotonic()
                elapsed_s = now - started_at
                if deadline is not None and now >= deadline:
                    raise RuntimeError(
                        "GNSS-SDR did not open its IQ FIFO reader within "
                        f"{timeout_value:.1f} seconds. The process is still running; "
                        "a cold FFTW plan after changing sample rate may need more time. "
                        "Set gnss_sdr_startup_timeout_s to 0 to wait without a deadline."
                    ) from exc

                if now >= next_progress_log_at:
                    timeout_label = (
                        "none" if timeout_value is None else f"{timeout_value:.1f}s"
                    )
                    self._report_startup(
                        "GNSS-SDR is alive and still initializing: elapsed=%.1fs "
                        "timeout=%s sample_rate=%.3f Msps. On this build, a prolonged "
                        "pre-FIFO wait means FFTW is measuring missing plans for the "
                        "selected rate. Leave it running; this is normally a one-time "
                        "cache build.",
                        elapsed_s,
                        timeout_label,
                        float(self._cfg.sample_rate) / 1e6,
                    )
                    next_progress_log_at = now + _FIFO_STARTUP_PROGRESS_INTERVAL_S

                time.sleep(0.05)
                continue

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
        paths = tuple(getattr(self, "_fifo_paths", (self._fifo_path,)))
        for path in paths:
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
            except OSError:
                pass
