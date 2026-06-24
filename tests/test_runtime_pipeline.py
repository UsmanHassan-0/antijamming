from __future__ import annotations

import queue

from antijamming.runtime import put_latest


def test_put_latest_replaces_stale_item_when_mailbox_is_full() -> None:
    mailbox: queue.Queue[int | None] = queue.Queue(maxsize=1)
    mailbox.put_nowait(1)

    put_latest(mailbox, 2)

    assert mailbox.qsize() == 1
    assert mailbox.get_nowait() == 2


def test_put_latest_allows_shutdown_sentinel_to_displace_old_work() -> None:
    mailbox: queue.Queue[int | None] = queue.Queue(maxsize=1)
    mailbox.put_nowait(99)

    put_latest(mailbox, None)

    assert mailbox.qsize() == 1
    assert mailbox.get_nowait() is None
