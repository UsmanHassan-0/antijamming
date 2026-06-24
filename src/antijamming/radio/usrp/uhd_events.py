"""UHD console marker monitoring.

UHD can emit single-character realtime markers such as ``D`` and ``O`` to
stderr without a newline. The shell launcher captures those bytes in
``logs/uhd_console.log``; this module mirrors them into timestamped runtime
logs so later audits can correlate them with PRN, FIFO, and recv timing.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time


@dataclass
class UhdMarkerEvent:
    marker: str
    count: int
    byte_offset: int


class UhdConsoleMarkerScanner:
    """Extract line-prefix UHD marker runs from appended console text."""

    def __init__(self) -> None:
        self._at_line_start = True
        self._candidate = ""
        self._candidate_offset = 0
        self._absolute_offset = 0

    def feed(self, text: str) -> list[UhdMarkerEvent]:
        events: list[UhdMarkerEvent] = []
        for char in text:
            if char in "\r\n":
                events.extend(self._flush_candidate(force=True))
                self._at_line_start = True
                self._absolute_offset += 1
                continue

            if char in "DO" and (self._at_line_start or self._candidate):
                if not self._candidate:
                    self._candidate_offset = self._absolute_offset
                self._candidate += char
                self._at_line_start = False
                self._absolute_offset += 1
                continue

            events.extend(self._flush_candidate(force=not char.isalpha()))
            self._at_line_start = False
            self._absolute_offset += 1
        return events

    def flush(self) -> list[UhdMarkerEvent]:
        return self._flush_candidate(force=True)

    def reset_offset(self) -> None:
        self._at_line_start = True
        self._candidate = ""
        self._candidate_offset = 0
        self._absolute_offset = 0

    def _flush_candidate(self, *, force: bool) -> list[UhdMarkerEvent]:
        candidate = self._candidate
        if not candidate:
            return []
        self._candidate = ""
        if not force and len(candidate) < 2:
            return []

        events: list[UhdMarkerEvent] = []
        start = 0
        while start < len(candidate):
            marker = candidate[start]
            end = start + 1
            while end < len(candidate) and candidate[end] == marker:
                end += 1
            events.append(
                UhdMarkerEvent(
                    marker=marker,
                    count=end - start,
                    byte_offset=self._candidate_offset + start,
                )
            )
            start = end
        return events


class UhdConsoleMarkerMonitor:
    """Background tailer for UHD marker bytes captured by ``run_realtime.sh``."""

    def __init__(
        self,
        log_path: Path,
        loggers: dict[str, logging.Logger],
        *,
        poll_interval_s: float = 0.05,
        summary_interval_s: float = 1.0,
        sample_rate_hz: float | None = None,
        channel_count: int | None = None,
        samples_per_chunk: int | None = None,
    ) -> None:
        self._log_path = log_path.expanduser().resolve()
        self._transport_log = loggers["transport"]
        self._health_log = loggers["health"]
        self._errors_log = loggers["errors"]
        self._poll_interval_s = max(0.01, float(poll_interval_s))
        self._summary_interval_s = max(0.1, float(summary_interval_s))
        self._sample_rate_hz = None if sample_rate_hz is None else float(sample_rate_hz)
        self._channel_count = None if channel_count is None else int(channel_count)
        self._samples_per_chunk = None if samples_per_chunk is None else int(samples_per_chunk)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scanner = UhdConsoleMarkerScanner()
        self._totals = {"D": 0, "O": 0}
        self._window_counts = {"D": 0, "O": 0}
        self._started_monotonic = 0.0
        self._window_started_monotonic = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_monotonic = time.monotonic()
        self._window_started_monotonic = self._started_monotonic
        self._window_counts = {"D": 0, "O": 0}
        self._thread = threading.Thread(
            target=self._run,
            name="uhd_console_marker_monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout_s)))
        self._thread = None
        for event in self._scanner.flush():
            self._log_event(event)
        self._log_summary(force=True)

    def _run(self) -> None:
        position = 0
        while not self._stop.is_set():
            try:
                if not self._log_path.exists():
                    time.sleep(self._poll_interval_s)
                    continue
                size = self._log_path.stat().st_size
                if size < position:
                    position = 0
                    self._scanner.reset_offset()
                if size == position:
                    time.sleep(self._poll_interval_s)
                    continue
                with self._log_path.open("rb") as handle:
                    handle.seek(position)
                    data = handle.read(max(1, size - position))
                    position = handle.tell()
                text = data.decode("utf-8", errors="replace")
                for event in self._scanner.feed(text):
                    self._log_event(event)
                self._log_summary(force=False)
            except Exception as exc:
                self._errors_log.error("UHD console marker monitor failed: %s", exc)
                time.sleep(self._poll_interval_s)

    def _log_event(self, event: UhdMarkerEvent) -> None:
        marker = event.marker
        if marker not in self._totals:
            return
        self._totals[marker] += int(event.count)
        self._window_counts[marker] += int(event.count)
        meaning = _marker_meaning(marker)
        elapsed_s = time.monotonic() - self._started_monotonic
        message = (
            "UHD console marker: marker=%s count=%d total_D=%d total_O=%d "
            "window_D=%d window_O=%d byte_offset=%d monitor_elapsed_s=%.3f "
            "meaning=%s source=%s"
        )
        args = (
            marker,
            int(event.count),
            int(self._totals["D"]),
            int(self._totals["O"]),
            int(self._window_counts["D"]),
            int(self._window_counts["O"]),
            int(event.byte_offset),
            elapsed_s,
            meaning,
            self._log_path,
        )
        self._transport_log.warning(message, *args)
        self._health_log.warning(message, *args)

    def _log_summary(self, *, force: bool) -> None:
        if self._window_started_monotonic <= 0.0:
            return
        now = time.monotonic()
        elapsed_s = now - self._window_started_monotonic
        if not force and elapsed_s < self._summary_interval_s:
            return
        d_count = int(self._window_counts["D"])
        o_count = int(self._window_counts["O"])
        if d_count <= 0 and o_count <= 0:
            self._window_started_monotonic = now
            return
        window_s = max(1e-9, elapsed_s)
        message = (
            "UHD console marker window: window_s=%.3f D=%d O=%d D_per_s=%.3f "
            "O_per_s=%.3f total_D=%d total_O=%d sample_rate_hz=%s "
            "channel_count=%s samples_per_chunk=%s samples_per_D=unknown "
            "note=%s source=%s"
        )
        args = (
            window_s,
            d_count,
            o_count,
            d_count / window_s,
            o_count / window_s,
            int(self._totals["D"]),
            int(self._totals["O"]),
            _format_optional_number(self._sample_rate_hz),
            "--" if self._channel_count is None else str(self._channel_count),
            "--" if self._samples_per_chunk is None else str(self._samples_per_chunk),
            "UHD D/O console markers do not encode an exact dropped-sample count.",
            self._log_path,
        )
        self._transport_log.warning(message, *args)
        self._health_log.warning(message, *args)
        self._window_counts = {"D": 0, "O": 0}
        self._window_started_monotonic = now


def _marker_meaning(marker: str) -> str:
    if marker == "D":
        return "dropped_samples_or_packets"
    if marker == "O":
        return "overflow"
    return "unknown"


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:.3f}"


__all__ = [
    "UhdConsoleMarkerMonitor",
    "UhdConsoleMarkerScanner",
    "UhdMarkerEvent",
]
