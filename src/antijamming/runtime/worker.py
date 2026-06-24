"""Qt worker adapter around the threaded backend runtime."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from antijamming.config import StreamConfig
from antijamming.runtime.backend import BackendRuntime


# =============================================================================
# Qt Worker Adapter
# =============================================================================

# The GUI owns this QThread object, but the backend owns SDR, DSP, and GNSS
# resources. Signals are the only GUI-facing data boundary.

class StreamWorker(QThread):
    """Expose backend runtime controls through Qt signals and thread lifecycle."""

    data_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, config: StreamConfig, loggers: dict[str, logging.Logger]) -> None:
        super().__init__()
        # Backend callbacks emit Qt signals from the worker thread. Qt handles
        # delivery to the main thread when connected to GUI slots.
        self._backend = BackendRuntime(
            config=config,
            loggers=loggers,
            on_data=self.data_ready.emit,
            on_status=self.status.emit,
            on_failed=self.failed.emit,
        )

    def run(self) -> None:
        self._backend.run()

    def stop(self, reason: str = "normal stop") -> None:
        self._backend.stop(reason)

    def set_doa_method(self, method: str) -> None:
        self._backend.set_doa_method(method)

    def set_expected_sources(self, count: int) -> None:
        self._backend.set_expected_sources(count)

    def set_algorithm_mode(self, mode: str) -> None:
        self._backend.set_algorithm_mode(mode)

    def set_jammer_detection_enabled(self, enabled: bool) -> None:
        self._backend.set_jammer_detection_enabled(enabled)
