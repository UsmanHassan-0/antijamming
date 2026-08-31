from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time

import pytest


def _wait_for_path(path: Path, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def test_sidecar_sigterm_finalizes_once_and_reaps_monitor_groups(
    tmp_path: Path,
) -> None:
    gui = subprocess.Popen(["sleep", "30"])
    sidecar: subprocess.Popen[str] | None = None
    try:
        env = os.environ.copy()
        env.update(
            {
                "ROOT": str(tmp_path),
                "IFACE": "lo",
                "INTERVAL": "1",
            }
        )
        sidecar = subprocess.Popen(
            [
                "bash",
                str(Path("tools/run_realtime_sidecar.sh").resolve()),
                str(gui.pid),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        current = tmp_path / "logs/sidecar/current"
        _wait_for_path(current / "manifest.txt")
        _wait_for_path(current / "nic_stats.pid")
        monitor_group_ids = [
            int(path.read_text(encoding="utf-8").strip())
            for path in current.glob("*.pid")
        ]

        sidecar.send_signal(signal.SIGTERM)
        stdout, stderr = sidecar.communicate(timeout=10)
        assert sidecar.returncode == 143, (stdout, stderr)

        manifest = (current / "manifest.txt").read_text(encoding="utf-8")
        assert manifest.count("sidecar_stopping_utc=") == 1
        assert manifest.count("sidecar_stopped_utc=") == 1
        for group_id in monitor_group_ids:
            with pytest.raises(ProcessLookupError):
                os.killpg(group_id, 0)
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.kill()
            sidecar.wait(timeout=5)
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=5)
