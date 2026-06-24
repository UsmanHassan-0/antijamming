"""GNSS-SDR executable resolution, process launch args, and scoped cleanup."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .models import _GnssSdrProcessInfo

class ProcessMixin:
    def _resolve_local_executable(self) -> Path | None:
        candidates: list[Path] = []
        if self._cfg.gnss_sdr_executable is not None:
            candidates.append(self._cfg.gnss_sdr_executable)
        candidates.extend(
            [
                self._cfg.gnss_sdr_build_dir / "src" / "main" / "gnss-sdr",
                self._cfg.gnss_sdr_install_dir / "gnss-sdr",
                self._cfg.gnss_sdr_install_dir / "bin" / "gnss-sdr",
                self._cfg.gnss_sdr_repo_dir
                / "build-usman"
                / "src"
                / "main"
                / "gnss-sdr",
                self._cfg.gnss_sdr_repo_dir / "build" / "src" / "main" / "gnss-sdr",
            ]
        )

        for candidate in candidates:
            path = Path(candidate).expanduser().resolve()
            if path.is_file() and os.access(path, os.X_OK):
                return path
        return None

    def _system_gnss_sdr_path(self) -> Path | None:
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            if not path_dir:
                continue
            candidate = Path(path_dir) / "gnss-sdr"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        return None

    def _gnss_sdr_launch_args(self, exe_path: Path) -> list[str]:
        """Return GNSS-SDR subprocess arguments for the product runtime."""
        args = [
            str(exe_path),
            f"--config_file={self._config_path}",
            "--logtostderr=1",
        ]
        stdbuf = shutil.which("stdbuf")
        if stdbuf:
            return [stdbuf, "-oL", "-eL", *args]
        return args

    def _terminate_matching_stale_processes(self) -> None:
        matches = self._matching_gnss_sdr_processes()
        if not matches:
            return
        for process in matches:
            command = " ".join(process.cmdline)
            self._log.warning(
                "Stale GNSS-SDR process detected: pid=%d command=%s",
                process.pid,
                command,
            )
            self._terminate_stale_process(process)

    def _matching_gnss_sdr_processes(self) -> list[_GnssSdrProcessInfo]:
        current_pid = os.getpid()
        proc_dir = Path("/proc")
        if not proc_dir.exists():
            return []
        matches: list[_GnssSdrProcessInfo] = []
        for path in proc_dir.iterdir():
            if not path.name.isdigit():
                continue
            pid = int(path.name)
            if pid == current_pid:
                continue
            try:
                raw_cmdline = (path / "cmdline").read_bytes()
            except OSError:
                continue
            if not raw_cmdline:
                continue
            cmdline = tuple(part.decode("utf-8", errors="replace") for part in raw_cmdline.split(b"\0") if part)
            if self._cmdline_matches_runtime(cmdline):
                matches.append(_GnssSdrProcessInfo(pid=pid, cmdline=cmdline))
        return matches

    def _cmdline_matches_runtime(self, cmdline: tuple[str, ...]) -> bool:
        if not cmdline:
            return False
        executable = Path(cmdline[0]).name
        if executable != "gnss-sdr":
            return False
        config_paths, runtime_dirs = self._owned_gnss_sdr_session_paths()
        for arg in cmdline[1:]:
            if arg.startswith("--config_file="):
                value = arg.split("=", 1)[1]
                try:
                    resolved_value = Path(value).expanduser().resolve()
                except OSError:
                    resolved_value = Path(value)
                if resolved_value in config_paths:
                    return True
            try:
                resolved_arg = Path(arg).expanduser().resolve()
            except OSError:
                resolved_arg = Path(arg)
            if resolved_arg in config_paths:
                return True
            if any(str(runtime_dir) in arg for runtime_dir in runtime_dirs):
                return True
        return False

    def _owned_gnss_sdr_session_paths(self) -> tuple[set[Path], set[Path]]:
        runtime_dirs = {self._runtime_dir}
        repo_runtime_dir = (
            self._cfg.gnss_sdr_repo_dir.expanduser().resolve()
            / "logs"
            / "runtime"
            / "fifo-x300"
        )
        runtime_dirs.add(repo_runtime_dir)
        config_paths = {runtime_dir / "fifo_gps_l1.conf" for runtime_dir in runtime_dirs}
        config_paths.add(self._config_path)
        return config_paths, runtime_dirs

    def _terminate_stale_process(self, process: _GnssSdrProcessInfo) -> None:
        try:
            self._terminate_process_group_or_process(process.pid)
        except ProcessLookupError:
            self._log.info("Stale GNSS-SDR process already exited: pid=%d", process.pid)
            return
        except OSError as exc:
            self._err_log.error(
                "Failed to terminate stale GNSS-SDR process pid=%d: %s",
                process.pid,
                exc,
            )
            return
        if self._wait_for_pid_exit(process.pid, timeout_s=3.0):
            self._log.warning("Stale GNSS-SDR process terminated: pid=%d", process.pid)
            return
        try:
            self._kill_process_group_or_process(process.pid)
        except ProcessLookupError:
            self._log.info("Stale GNSS-SDR process exited before kill: pid=%d", process.pid)
            return
        except OSError as exc:
            self._err_log.error(
                "Failed to kill stale GNSS-SDR process pid=%d: %s",
                process.pid,
                exc,
            )
            return
        if self._wait_for_pid_exit(process.pid, timeout_s=2.0):
            self._log.warning("Stale GNSS-SDR process killed: pid=%d", process.pid)
        else:
            self._err_log.error("Stale GNSS-SDR process survived kill: pid=%d", process.pid)

    def _terminate_process_group_or_process(self, pid: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            raise
        except OSError:
            os.kill(pid, 15)
            return
        if pgid == pid:
            os.killpg(pgid, 15)
        else:
            os.kill(pid, 15)

    def _kill_process_group_or_process(self, pid: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            raise
        except OSError:
            os.kill(pid, 9)
            return
        if pgid == pid:
            os.killpg(pgid, 9)
        else:
            os.kill(pid, 9)

    def _wait_for_pid_exit(self, pid: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except OSError:
                return True
            time.sleep(0.05)
        return False

    def _reset_runtime_dir(self) -> None:
        self._clear_dir(self._runtime_dir)
        if self._log_dir != self._runtime_dir:
            self._clear_dir(self._log_dir)

    def _clear_dir(self, base_dir: Path) -> None:
        base_dir.mkdir(parents=True, exist_ok=True)
        for path in base_dir.iterdir():
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except FileNotFoundError:
                continue
