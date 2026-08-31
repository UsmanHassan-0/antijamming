from __future__ import annotations

import sys
import threading
import time

import numpy as np

from antijamming.app.headless import HeadlessRuntimeService
from antijamming.runtime.ipc import JsonIpcClient, JsonIpcServer, metrics_for_wire


def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


def test_headless_module_does_not_import_qt() -> None:
    before = set(sys.modules)

    __import__("antijamming.app.headless")

    newly_loaded = set(sys.modules) - before
    assert not any(name == "PyQt6" or name.startswith("PyQt6.") for name in newly_loaded)


def test_wire_metrics_drop_iq_previews_and_convert_numpy() -> None:
    result = metrics_for_wire(
        {
            "ui_metrics_seq": np.int64(7),
            "doa_raw_spectrum": np.asarray([1.0, np.nan, 3.0]),
            "complex_samples": np.ones((4, 8192), dtype=np.complex64),
            "gnss_snapshot": {"tracking_count": np.int32(2)},
        }
    )

    assert result == {
        "ui_metrics_seq": 7,
        "doa_raw_spectrum": [1.0, None, 3.0],
        "gnss_snapshot": {"tracking_count": 2},
    }


def test_headless_service_stays_idle_until_explicit_start_command(tmp_path) -> None:
    created = []

    class FakeBackend:
        def __init__(self, **kwargs) -> None:
            self.running = False
            self.starts = 0
            self.stops = 0
            self.stopped = threading.Event()
            created.append(self)

        def is_running(self) -> bool:
            return self.running

        def start(self) -> None:
            self.starts += 1
            self.running = True
            self.stopped.clear()

        def stop(self, _reason: str) -> None:
            self.stops += 1
            self.running = False
            self.stopped.set()

        def wait(self, timeout=None) -> bool:
            return self.stopped.wait(timeout)

        def set_expected_sources(self, _count: int) -> None:
            pass

        def set_lcmv_test_enabled(self, _enabled: bool) -> None:
            pass

        def mark_rf_event(self, event: str, **kwargs) -> dict:
            return {"event": event, **kwargs}

    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-test")},
        tmp_path / "idle.sock",
        backend_factory=FakeBackend,  # type: ignore[arg-type]
    )
    backend = created[0]

    assert backend.starts == 0
    assert service._handle_command("ping", {})["backend_running"] is False
    assert backend.starts == 0

    service._handle_command("start", {"reason": "unit test"})
    assert backend.starts == 1
    service._handle_command("stop", {"reason": "unit test"})
    assert backend.stops == 1

    marker = service._handle_command(
        "mark_rf_event",
        {
            "event": "jammer_on",
            "attenuation_db": "50",
            "bladeRF_gain_db": 50,
            "notes": "unit test",
            "source": "gui",
        },
    )
    assert marker["event"]["event"] == "jammer_on"
    assert marker["event"]["attenuation_db"] == 50.0


def test_json_ipc_round_trip_does_not_require_backend_or_hardware(tmp_path) -> None:
    commands: list[tuple[str, dict]] = []
    messages: list[dict] = []
    command_seen = threading.Event()

    def on_command(command: str, arguments: dict) -> dict:
        commands.append((command, arguments))
        command_seen.set()
        return {"accepted": True}

    server = JsonIpcServer(tmp_path / "headless.sock", on_command=on_command)
    client = JsonIpcClient(tmp_path / "headless.sock", on_message=messages.append)
    try:
        server.start()
        client.connect()
        client.command("ping", source="test")
        assert command_seen.wait(2.0)
        assert commands == [("ping", {"source": "test"})]

        server.publish_metrics(
            {
                "ui_metrics_seq": 11,
                "doa_raw_spectrum": np.asarray([0.25, 1.0]),
            }
        )
        _wait_for(
            lambda: any(
                message.get("type") == "metrics"
                and message.get("payload", {}).get("ui_metrics_seq") == 11
                for message in messages
            )
        )
    finally:
        client.close()
        server.close()


def test_json_ipc_server_close_joins_acceptor_and_sessions(tmp_path) -> None:
    socket_path = tmp_path / "headless.sock"
    server = JsonIpcServer(socket_path, on_command=lambda _command, _args: {})
    clients = [JsonIpcClient(socket_path) for _ in range(8)]
    server.start()
    accept_thread = server._accept_thread
    assert accept_thread is not None

    try:
        for client in clients:
            client.connect()
        _wait_for(lambda: len(server._session_snapshot()) == len(clients))
    finally:
        server.close()
        for client in clients:
            client.close()

    assert not accept_thread.is_alive()
    assert server._session_snapshot() == []
    assert not socket_path.exists()
