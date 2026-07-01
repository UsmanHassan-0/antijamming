"""GNSS-SDR stdout/glog draining and runtime-line routing."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import pty
import sys
import threading
import time

from .log_parsers import (
    _ANSI_ESCAPE_RE,
    _CONSOLE_FRAGMENT_RE,
    _GLOG_LINE_RE,
    _GNSS_DIAGNOSTIC_RE,
    _POSITION_FIX_RE,
    _RECEIVER_TIME_RE,
    _flush_log_handle,
)

class OutputMonitorMixin:
    def _prepare_nmea_tty(self) -> None:
        self._close_nmea_tty_fds()
        self._nmea_tty_path = "/dev/null"
        if not bool(self._cfg.gnss_pvt_nmea_tty_enable):
            return

        try:
            master_fd, slave_fd = pty.openpty()
            self._nmea_master_fd = master_fd
            self._nmea_slave_fd = slave_fd
            self._nmea_tty_path = os.ttyname(slave_fd)
        except OSError as exc:
            self._close_nmea_tty_fds()
            self._err_log.error("Failed creating GNSS-SDR NMEA PTY: %s", exc)
            self._nmea_tty_path = "/dev/null"

    def _start_nmea_tty_reader(self) -> None:
        if not bool(self._cfg.gnss_pvt_nmea_tty_enable):
            return
        if self._nmea_master_fd is None or self._nmea_tty_path in {None, "/dev/null"}:
            return
        if self._nmea_thread is not None and self._nmea_thread.is_alive():
            return
        self._nmea_thread = threading.Thread(
            target=self._drain_nmea_tty,
            name="gnss_sdr_nmea_tty",
            daemon=True,
        )
        self._nmea_thread.start()
        self._handoff_log.info(
            "GNSS-SDR NMEA tty monitor active: devname=%s file_output=%s rate_ms=%d",
            self._nmea_tty_path,
            bool(self._cfg.gnss_pvt_nmea_output_file_enable),
            max(100, int(self._cfg.gnss_pvt_nmea_rate_ms)),
        )

    def _stop_nmea_tty_reader(self) -> None:
        self._close_nmea_tty_fds()
        if self._nmea_thread is not None:
            self._nmea_thread.join(timeout=1.0)
            self._nmea_thread = None

    def _close_nmea_tty_fds(self) -> None:
        for attr in ("_nmea_master_fd", "_nmea_slave_fd"):
            fd = getattr(self, attr, None)
            setattr(self, attr, None)
            if fd is None:
                continue
            try:
                os.close(int(fd))
            except OSError:
                pass

    def _drain_nmea_tty(self) -> None:
        fd = self._nmea_master_fd
        if fd is None:
            return
        line_buffer = ""
        try:
            while not self._monitor_stop.is_set():
                try:
                    chunk = os.read(fd, 4096)
                except OSError as exc:
                    if exc.errno not in {errno.EBADF, errno.EIO}:
                        self._err_log.error("Failed reading GNSS-SDR NMEA tty: %s", exc)
                    break
                if not chunk:
                    break
                text_chunk = chunk.decode("ascii", errors="ignore")
                for char in text_chunk:
                    if char in "\r\n":
                        self._process_nmea_record(line_buffer)
                        line_buffer = ""
                    else:
                        line_buffer += char
            self._process_nmea_record(line_buffer)
        except Exception as exc:
            self._err_log.error("Failed monitoring GNSS-SDR NMEA tty: %s", exc)

    def _process_nmea_record(self, record: str) -> None:
        text = record.strip()
        if not text:
            return
        self._handle_nmea_line(text)
        with self._state_lock:
            self._nmea_tty_line_count += 1
            self._nmea_tty_last_monotonic_s = time.monotonic()

    def _drain_stdout(self) -> None:
        if self._proc is None or self._stdout_handle is None:
            return
        try:
            self._receiver_log_path.parent.mkdir(parents=True, exist_ok=True)
            with (
                self._console_log_path.open("a", encoding="utf-8", errors="replace") as console_handle,
                self._receiver_log_path.open("a", encoding="utf-8", errors="replace") as receiver_handle,
            ):
                line_buffer = ""
                while True:
                    # Drain the PTY in chunks. Byte-at-a-time reads and forced
                    # disk syncs can make GNSS-SDR block on stdout during noisy
                    # acquisition/loss-of-lock bursts, which then backs up the IQ FIFO.
                    chunk = self._read_stdout_chunk()
                    if not chunk:
                        break
                    if isinstance(chunk, bytes):
                        text_chunk = chunk.decode("utf-8", errors="replace")
                    else:
                        text_chunk = str(chunk)
                    for char in text_chunk:
                        if char in "\r\n":
                            self._process_console_record(
                                line_buffer,
                                char,
                                console_handle=console_handle,
                                receiver_handle=receiver_handle,
                            )
                            line_buffer = ""
                        else:
                            line_buffer += char
                self._process_console_record(
                    line_buffer,
                    "",
                    console_handle=console_handle,
                    receiver_handle=receiver_handle,
                )
        except OSError as exc:
            if exc.errno != errno.EIO:
                self._err_log.error("Failed reading GNSS-SDR output: %s", exc)
        except Exception as exc:
            self._err_log.error("Failed reading GNSS-SDR output: %s", exc)

    def _read_stdout_chunk(self):
        if self._stdout_handle is None:
            return b""
        try:
            fd = self._stdout_handle.fileno()
        except (AttributeError, OSError, ValueError):
            return self._stdout_handle.read(4096)
        return os.read(fd, 4096)

    def _process_console_record(
        self,
        record: str,
        delimiter: str,
        *,
        console_handle=None,
        receiver_handle=None,
    ) -> None:
        text = _ANSI_ESCAPE_RE.sub("", record).strip()
        if not text:
            return
        self._handle_runtime_line(text)
        output_delimiter = "\n" if delimiter == "\r" else delimiter
        if self._should_route_to_receiver_log(text):
            if receiver_handle is not None:
                receiver_handle.write(text + output_delimiter)
                _flush_log_handle(receiver_handle)
            return
        if console_handle is not None:
            console_handle.write(text + output_delimiter)
            _flush_log_handle(console_handle)
        if bool(getattr(self._cfg, "gnss_sdr_echo_stdout", False)):
            sys.stdout.write(text + output_delimiter)
            sys.stdout.flush()

    def _should_echo_stdout_line(self, text: str) -> bool:
        return not self._should_route_to_receiver_log(text)

    def _should_route_to_receiver_log(self, text: str) -> bool:
        if _GLOG_LINE_RE.search(text) is not None:
            return True
        if _GNSS_DIAGNOSTIC_RE.search(text) is not None:
            return True
        return len(text) <= 16 and _CONSOLE_FRAGMENT_RE.fullmatch(text) is not None

    def _monitor_glog_files(self) -> None:
        current_path: Path | None = None
        current_handle = None
        try:
            while not self._monitor_stop.is_set():
                candidate = self._latest_session_glog_path()
                if candidate is not None and candidate != current_path:
                    if current_handle is not None:
                        current_handle.close()
                    current_path = candidate
                    current_handle = current_path.open("r", encoding="utf-8", errors="replace")
                    self._handoff_log.info("GNSS receiver glog file: %s", current_path)
                if current_handle is not None:
                    line = current_handle.readline()
                    if line:
                        self._handle_runtime_line(line.rstrip())
                        continue
                time.sleep(0.05)
        except Exception as exc:
            self._err_log.error("Failed monitoring GNSS-SDR glog files: %s", exc)
        finally:
            if current_handle is not None:
                current_handle.close()

    def _latest_session_glog_path(self) -> Path | None:
        candidates = sorted(
            self._log_dir.glob("gnss-sdr*.INFO*"),
            key=lambda path: path.stat().st_mtime,
        )
        for candidate in reversed(candidates):
            try:
                if candidate.stat().st_mtime >= (self._session_epoch_s - 1.0):
                    return candidate
            except OSError:
                continue
        return None

    def _handle_runtime_line(self, text: str) -> None:
        if not text:
            return
        receiver_match = _RECEIVER_TIME_RE.search(text)
        if receiver_match:
            hours = int(receiver_match.group(1)) if receiver_match.group(1) is not None else 0
            minutes = int(receiver_match.group(2)) if receiver_match.group(2) is not None else 0
            seconds = int(receiver_match.group(3))
            with self._state_lock:
                self._receiver_time_s = (hours * 3600) + (minutes * 60) + seconds
        position_match = _POSITION_FIX_RE.search(text)
        if position_match:
            self._mark_pvt_observed(observation_count=int(position_match.group(1)))
        self._handle_receiver_event_line(text)
        self._handle_prn_state_line(text)
