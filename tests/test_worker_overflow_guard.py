from __future__ import annotations

import logging

from antijamming.config import StreamConfig
from antijamming.runtime import StreamWorker


def _build_loggers() -> dict[str, logging.Logger]:
    keys = ["app", "hw", "stream", "transport", "phase", "doa", "lcmv", "gnss", "health", "errors"]
    return {k: logging.getLogger(f"test.{k}") for k in keys}


def test_worker_overflow_guard_triggers_on_total() -> None:
    cfg = StreamConfig(max_overflow_streak=99, max_total_overflow=5, stop_on_overflow=True)
    worker = StreamWorker(cfg, _build_loggers())
    worker._backend._overflow_count = 5
    assert worker._backend._config.stop_on_overflow is True
    assert worker._backend._overflow_count >= worker._backend._config.max_total_overflow


def test_worker_overflow_guard_disabled() -> None:
    cfg = StreamConfig(max_total_overflow=1, stop_on_overflow=False)
    worker = StreamWorker(cfg, _build_loggers())
    worker._backend._overflow_count = 20
    assert worker._backend._config.stop_on_overflow is False
