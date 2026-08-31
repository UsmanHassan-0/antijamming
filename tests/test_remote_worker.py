from __future__ import annotations

from pathlib import Path

from antijamming.runtime.remote_worker import RemoteStreamWorker


class _CommandClient:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str | None, dict[str, object]]] = []

    def command(
        self,
        command: str,
        *,
        request_id: str | None = None,
        **arguments: object,
    ) -> str:
        self.commands.append((command, request_id, arguments))
        return str(request_id)


def test_start_reply_failure_clears_pending_state() -> None:
    worker = RemoteStreamWorker(Path("/unused"))
    client = _CommandClient()
    worker._client = client  # type: ignore[assignment]

    worker.start()
    request_id = client.commands[0][1]
    assert request_id is not None
    assert worker.isRunning()
    assert not worker.wait(0)

    worker._on_message(
        {
            "type": "reply",
            "request_id": request_id,
            "ok": False,
            "error": "start rejected",
        }
    )

    assert not worker.isRunning()
    assert worker.wait(0)
    assert worker._pending_start_request_id is None


def test_runtime_failure_before_running_releases_waiter() -> None:
    worker = RemoteStreamWorker(Path("/unused"))
    client = _CommandClient()
    worker._client = client  # type: ignore[assignment]
    worker.start()

    worker._on_message({"type": "failed", "message": "startup failed"})

    assert not worker.isRunning()
    assert worker.wait(0)
    assert worker._pending_start_request_id is None
