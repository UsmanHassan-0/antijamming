"""Latest-only queue helper for realtime worker stages."""

from __future__ import annotations

import queue
from typing import TypeVar

T = TypeVar("T")


def put_latest(q: "queue.Queue[T | None]", item: T | None) -> None:
    """Put the newest item into a bounded queue, dropping stale queued work."""
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass

    try:
        q.get_nowait()
    except queue.Empty:
        pass

    try:
        q.put_nowait(item)
    except queue.Full:
        # Another producer/consumer beat us; the newer item is already in flight.
        pass


__all__ = ["put_latest"]
