from __future__ import annotations

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


def _device_for_fake_usrp(fake_usrp: _FakeUsrp) -> UsrpRxDevice:
    device = UsrpRxDevice.__new__(UsrpRxDevice)
    device._usrp = fake_usrp
    return device


def test_fpga_image_flavor_parser_detects_xg_bit_path() -> None:
    assert (
        _fpga_image_flavor_from_text("FPGA path: /usr/share/uhd/images/usrp_x300_fpga_XG.bit")
        == "XG"
    )


def test_x300_fpga_image_check_accepts_reported_xg() -> None:
    device = _device_for_fake_usrp(
        _FakeUsrp(sensors={"fpga_image": "/usr/share/uhd/images/usrp_x300_fpga_XG.bit"})
    )

    assert device._verify_x300_fpga_image() == ["Mboard0 FPGA image: XG"]


def test_x300_fpga_image_check_rejects_non_xg_report() -> None:
    device = _device_for_fake_usrp(
        _FakeUsrp(sensors={"fpga_image": "fpga: hg"})
    )

    try:
        device._verify_x300_fpga_image()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("non-XG FPGA image should be rejected")

    assert "requires XG" in message
    assert "./setup.sh" in message


def test_x300_fpga_image_check_reports_unknown_when_uhd_omits_flavor() -> None:
    device = _device_for_fake_usrp(_FakeUsrp(sensors={"fpga_version": "38.0"}))

    assert device._verify_x300_fpga_image() == [
        "Mboard FPGA image: not reported by UHD (mboard_indices=0); "
        "XG could not be verified from runtime metadata."
    ]
