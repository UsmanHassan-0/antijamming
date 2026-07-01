from __future__ import annotations

from antijamming.radio.transport import extract_ipv4_from_usrp_addr
from antijamming.radio.usrp import usrp_arg_int, with_usrp_frame_sizes


def test_extract_ipv4_from_usrp_addr() -> None:
    assert extract_ipv4_from_usrp_addr("addr=192.168.40.2") == "192.168.40.2"
    assert extract_ipv4_from_usrp_addr("type=x300,addr=192.168.40.2") == "192.168.40.2"
    assert extract_ipv4_from_usrp_addr("no ip here") is None


def test_with_usrp_frame_sizes_adds_fixed_product_transport_args() -> None:
    assert (
        with_usrp_frame_sizes(
            "addr=192.168.40.2",
            recv_frame_size=8000,
            send_frame_size=8000,
            recv_buff_size=50_000_000,
            num_recv_frames=4096,
        )
        == "addr=192.168.40.2,recv_frame_size=8000,send_frame_size=8000,"
        "recv_buff_size=50000000,num_recv_frames=4096"
    )


def test_with_usrp_frame_sizes_preserves_explicit_transport_args() -> None:
    assert (
        with_usrp_frame_sizes(
            "addr=192.168.40.2,recv_buff_size=1000,num_recv_frames=32",
            recv_frame_size=8000,
            send_frame_size=8000,
            recv_buff_size=50_000_000,
            num_recv_frames=4096,
        )
        == "addr=192.168.40.2,recv_buff_size=1000,num_recv_frames=32,"
        "recv_frame_size=8000,send_frame_size=8000"
    )


def test_usrp_arg_int_reads_transport_arg() -> None:
    assert usrp_arg_int("addr=192.168.40.2,recv_frame_size=8000", "recv_frame_size", 1) == 8000
    assert usrp_arg_int("addr=192.168.40.2", "recv_frame_size", 8000) == 8000
