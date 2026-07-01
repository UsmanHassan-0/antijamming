"""UHD address helpers for the fixed X300/XG product profile."""

from __future__ import annotations


def with_usrp_frame_sizes(
    usrp_addr: str,
    *,
    recv_frame_size: int,
    send_frame_size: int,
    recv_buff_size: int | None = None,
    num_recv_frames: int | None = None,
) -> str:
    """Append configured UHD transport arguments when they are absent."""

    parts = [part.strip() for part in str(usrp_addr).split(",") if part.strip()]
    keys = {part.split("=", 1)[0].strip() for part in parts if "=" in part}
    if int(recv_frame_size) > 0 and "recv_frame_size" not in keys:
        parts.append(f"recv_frame_size={int(recv_frame_size)}")
    if int(send_frame_size) > 0 and "send_frame_size" not in keys:
        parts.append(f"send_frame_size={int(send_frame_size)}")
    if (
        recv_buff_size is not None
        and int(recv_buff_size) > 0
        and "recv_buff_size" not in keys
    ):
        parts.append(f"recv_buff_size={int(recv_buff_size)}")
    if (
        num_recv_frames is not None
        and int(num_recv_frames) > 0
        and "num_recv_frames" not in keys
    ):
        parts.append(f"num_recv_frames={int(num_recv_frames)}")
    return ",".join(parts)


def usrp_arg_int(usrp_addr: str, key: str, default: int) -> int:
    """Read an integer UHD device argument from a comma-separated address."""

    for part in str(usrp_addr).split(","):
        name, sep, value = part.strip().partition("=")
        if sep and name == key:
            try:
                return int(value)
            except ValueError:
                return int(default)
    return int(default)
