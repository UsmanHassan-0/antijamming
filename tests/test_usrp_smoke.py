from __future__ import annotations

import os
import time

import pytest

from antijamming.config import default_stream_config
from antijamming.radio.usrp import UsrpRxDevice, validate_rx_chunk_result


@pytest.mark.usrp
@pytest.mark.skipif(
    os.environ.get("RUN_USRP_TESTS") != "1",
    reason="set RUN_USRP_TESTS=1 for the exclusive USRP hardware smoke test",
)
def test_usrp_smoke_recv_and_stop() -> None:
    cfg = default_stream_config()
    cfg.usrp_addr = os.environ.get("USRP_ADDR", cfg.usrp_addr)
    rate_override = os.environ.get("USRP_TEST_RATE")
    if rate_override is not None:
        cfg.sample_rate = float(rate_override)
        cfg.usrp_rx_bandwidth_hz = cfg.sample_rate
        cfg.min_sample_rate = cfg.sample_rate
    cfg.gain_db = float(os.environ.get("USRP_TEST_GAIN", "25.0"))
    cfg.samples_per_chunk = int(os.environ.get("USRP_TEST_CHUNK", "4096"))
    device = UsrpRxDevice(cfg)
    try:
        report = device.startup_report_lines()
        assert any("RX config:" in line for line in report)

        got_samples = 0
        states: list[str] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and got_samples == 0:
            result = validate_rx_chunk_result(
                device.recv_chunk(),
                expected_channels=len(cfg.channels),
            )
            states.append(result.state)
            assert result.chunk.shape[0] == len(cfg.channels)
            got_samples = int(result.chunk.shape[1])
            if result.state == "other":
                pytest.fail("USRP recv returned unknown metadata state 'other'")
        assert got_samples > 0, f"No samples received from USRP (states={states})"
    finally:
        device.stop()
