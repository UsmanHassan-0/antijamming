from __future__ import annotations

import os
import time

import pytest

from antijamming.config import default_stream_config
from antijamming.radio.usrp import UsrpRxDevice


@pytest.mark.usrp
@pytest.mark.skipif(
    os.environ.get("RUN_USRP_TESTS") != "1",
    reason="set RUN_USRP_TESTS=1 for the exclusive USRP hardware smoke test",
)
def test_usrp_smoke_recv_and_stop() -> None:
    cfg = default_stream_config()
    cfg.usrp_addr = os.environ.get("USRP_ADDR", cfg.usrp_addr)
    cfg.sample_rate = float(os.environ.get("USRP_TEST_RATE", "2e6"))
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
            chunk, state = device.recv_chunk()
            states.append(state)
            assert chunk.shape[0] == len(cfg.channels)
            got_samples = int(chunk.shape[1])
            if state == "other":
                pytest.fail("USRP recv returned unknown metadata state 'other'")
        assert got_samples > 0, f"No samples received from USRP (states={states})"
    finally:
        device.stop()
