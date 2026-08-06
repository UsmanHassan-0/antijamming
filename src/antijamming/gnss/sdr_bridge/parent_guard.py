"""Exec GNSS-SDR with a Linux parent-death signal."""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import sys


_PR_SET_PDEATHSIG = 1


def _set_parent_death_signal(signum: int) -> None:
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.prctl(_PR_SET_PDEATHSIG, int(signum), 0, 0, 0)
    if result != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command that exits when its parent process dies.",
    )
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    try:
        _set_parent_death_signal(signal.SIGTERM)
    except OSError as exc:
        print(f"parent_guard: could not set parent-death signal: {exc}", file=sys.stderr)

    if os.getppid() != int(args.parent_pid):
        return 143

    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
