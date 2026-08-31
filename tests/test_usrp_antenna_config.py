from __future__ import annotations

import pytest

from antijamming.config import StreamConfig
from antijamming.radio.usrp import UsrpRxDevice
from antijamming.radio.usrp.device import _fpga_image_flavor_from_text


def _device_for_config(cfg: StreamConfig) -> UsrpRxDevice:
    device = UsrpRxDevice.__new__(UsrpRxDevice)
    device._cfg = cfg
    return device


def test_default_twinrx_antenna_map_is_by_physical_channel() -> None:
    device = _device_for_config(StreamConfig())

    assert [device._rx_antenna_for_channel(ch) for ch in (0, 1, 2, 3)] == [
        "RX1",
        "RX2",
        "RX1",
        "RX2",
    ]


def test_forced_antenna_overrides_channel_map() -> None:
    device = _device_for_config(StreamConfig(antenna="rx2"))

    assert [device._rx_antenna_for_channel(ch) for ch in (0, 1, 2, 3)] == [
        "RX2",
        "RX2",
        "RX2",
        "RX2",
    ]


def test_default_twinrx_lo_sharing_map_matches_two_board_layout() -> None:
    device = _device_for_config(StreamConfig(twinrx_lo_sharing=True))

    assert [device._rx_lo_source_for_channel(ch) for ch in (0, 1, 2, 3)] == [
        "internal",
        "companion",
        "reimport",
        "reimport",
    ]
    assert [device._rx_lo_export_for_channel(ch) for ch in (0, 1, 2, 3)] == [
        True,
        False,
        True,
        False,
    ]


class _FakeSensor:
    def __init__(self, value: str) -> None:
        self._value = value

    def to_pp_string(self) -> str:
        return self._value

    def to_bool(self) -> bool:
        return self._value.lower() == "true"


class _FakeUsrp:
    def __init__(self, *, name: str = "X310", sensors: dict[str, str] | None = None) -> None:
        self._name = name
        self._sensors = sensors or {}

    def get_num_mboards(self) -> int:
        return 1

    def get_mboard_name(self, _mboard: int) -> str:
        return self._name

    def get_mboard_sensor_names(self, _mboard: int) -> list[str]:
        return list(self._sensors)

    def get_mboard_sensor(self, sensor_name: str, _mboard: int) -> _FakeSensor:
        return _FakeSensor(self._sensors[str(sensor_name)])


class _FakeLoUsrp:
    def __init__(self, lock_states: list[bool]) -> None:
        self._lock_states = list(lock_states)
        self.lock_queries = 0

    def get_rx_sensor_names(self, _channel: int) -> list[str]:
        return ["lo_locked"]

    def get_rx_sensor(self, _name: str, _channel: int) -> _FakeSensor:
        index = min(self.lock_queries, len(self._lock_states) - 1)
        self.lock_queries += 1
        return _FakeSensor(str(self._lock_states[index]))


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


def _device_for_fake_usrp(fake_usrp: _FakeUsrp) -> UsrpRxDevice:
    device = UsrpRxDevice.__new__(UsrpRxDevice)
    device._usrp = fake_usrp
    return device


def test_fpga_image_flavor_parser_detects_xg_bit_path() -> None:
    assert (
        _fpga_image_flavor_from_text("FPGA path: /usr/share/uhd/images/usrp_x300_fpga_XG.bit")
        == "XG"
    )


def test_fpga_image_flavor_parser_detects_hg_bit_path() -> None:
    assert (
        _fpga_image_flavor_from_text("FPGA path: /usr/share/uhd/images/usrp_x300_fpga_HG.bit")
        == "HG"
    )


def test_x300_fpga_image_check_accepts_reported_hg() -> None:
    device = _device_for_fake_usrp(
        _FakeUsrp(sensors={"fpga_image": "/usr/share/uhd/images/usrp_x300_fpga_HG.bit"})
    )

    assert device._verify_x300_fpga_image() == ["Mboard0 FPGA image: HG"]


def test_x300_fpga_image_check_rejects_non_hg_report() -> None:
    device = _device_for_fake_usrp(
        _FakeUsrp(sensors={"fpga_image": "fpga: xg"})
    )

    try:
        device._verify_x300_fpga_image()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("non-HG FPGA image should be rejected")

    assert "requires HG" in message
    assert "./setup.sh" in message


def test_x300_fpga_image_check_reports_unknown_when_uhd_omits_flavor() -> None:
    device = _device_for_fake_usrp(_FakeUsrp(sensors={"fpga_version": "38.0"}))

    assert device._verify_x300_fpga_image() == [
        "Mboard FPGA image: not reported by UHD (mboard_indices=0); "
        "HG could not be verified from runtime metadata."
    ]


def test_lo_lock_wait_polls_at_bounded_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    device = _device_for_config(StreamConfig(channels=(0,), lo_lock_timeout_s=1.0))
    device._usrp = _FakeLoUsrp([False, False, True])
    monkeypatch.setattr("antijamming.radio.usrp.device.time.monotonic", clock.monotonic)
    monkeypatch.setattr("antijamming.radio.usrp.device.time.sleep", clock.sleep)

    assert device._wait_for_lo_lock() == {0: True}
    assert clock.sleep_calls == [0.05, 0.05]
    assert device._lo_lock_wait_elapsed_s == pytest.approx(0.1)


def test_lo_lock_wait_times_out_after_bounded_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    device = _device_for_config(StreamConfig(channels=(0,), lo_lock_timeout_s=0.1))
    device._usrp = _FakeLoUsrp([False])
    monkeypatch.setattr("antijamming.radio.usrp.device.time.monotonic", clock.monotonic)
    monkeypatch.setattr("antijamming.radio.usrp.device.time.sleep", clock.sleep)

    with pytest.raises(RuntimeError, match=r"unlocked channels=\[0\]"):
        device._wait_for_lo_lock()

    assert clock.sleep_calls == [0.05, 0.05]
    assert device._lo_lock_wait_elapsed_s == pytest.approx(0.1)
