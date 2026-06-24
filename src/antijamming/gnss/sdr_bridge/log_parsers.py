"""Stateless GNSS-SDR console and glog parsing helpers."""

from __future__ import annotations

import re

def _flush_log_handle(handle) -> None:
    # Flush each mirrored GNSS-SDR line so readers see it promptly. Do not
    # fsync every line: during acquisition/loss-of-lock bursts that turns log
    # mirroring into a disk-latency source and can backpressure GNSS-SDR stdout.
    handle.flush()

# GNSS-SDR state is reconstructed from console and glog text. Regexes stay
# module-level so monitor threads do not repeatedly compile them.
_CONSTELLATION_NAME_TOKEN = r"GPS|Galileo|BeiDou|Beidou|BDS|GLONASS"
_CONSTELLATION_LETTER_TOKEN = r"[GECR]"
_PRN_NUMBER_TOKEN = rf"(?:{_CONSTELLATION_LETTER_TOKEN}\s*)?(\d+)"
_ASSIGNED_RE = re.compile(
    rf"Channel (\d+) assigned to (?:({_CONSTELLATION_NAME_TOKEN}) PRN\s+|({_CONSTELLATION_LETTER_TOKEN})\s+){_PRN_NUMBER_TOKEN}",
    re.IGNORECASE,
)
_ACQUIRED_RE = re.compile(
    rf"Successful acquisition in channel (\d+) for satellite ({_CONSTELLATION_LETTER_TOKEN})\s+(\d+)",
    re.IGNORECASE,
)
_ACQUISITION_DECISION_RE = re.compile(
    rf"Acquisition decision: (positive|negative), satellite ({_CONSTELLATION_LETTER_TOKEN})\s*(\d+)",
    re.IGNORECASE,
)
_ACQUISITION_METRIC_RE = re.compile(
    r"\b("
    r"test_statistics|threshold|threshold_margin|threshold_ratio|code_phase|"
    r"doppler|input_signal_power"
    r")\s+(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)
_TRACKING_RE = re.compile(
    rf"satellite (?:({_CONSTELLATION_NAME_TOKEN}) PRN\s+|({_CONSTELLATION_LETTER_TOKEN})\s+){_PRN_NUMBER_TOKEN}.+in channel (\d+)",
    re.IGNORECASE,
)
_TRACKING_STARTED_RE = re.compile(
    rf"Tracking of ({_CONSTELLATION_NAME_TOKEN}) .+ on channel (\d+) for satellite (?:{_CONSTELLATION_NAME_TOKEN}) PRN\s+{_PRN_NUMBER_TOKEN}",
    re.IGNORECASE,
)
_BIT_SYNC_LOCK_RE = re.compile(
    rf"({_CONSTELLATION_NAME_TOKEN}) .+ synchronization locked in channel (\d+) "
    rf"for satellite (?:{_CONSTELLATION_NAME_TOKEN}) PRN\s+{_PRN_NUMBER_TOKEN}",
    re.IGNORECASE,
)
_NAV_MESSAGE_RE = re.compile(
    rf"New ({_CONSTELLATION_NAME_TOKEN}) .* message received in channel (\d+).* satellite "
    rf"(?:{_CONSTELLATION_NAME_TOKEN}) PRN\s+{_PRN_NUMBER_TOKEN}\b"
    r".* CN0=([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*dB-Hz"
)
_POSITION_FIX_RE = re.compile(r"Position at .+ using (\d+) observations\b")
_LOSS_RE = re.compile(
    rf"Loss of lock in channel (\d+), satellite (?:({_CONSTELLATION_NAME_TOKEN}) PRN\s+|({_CONSTELLATION_LETTER_TOKEN})\s+){_PRN_NUMBER_TOKEN}",
    re.IGNORECASE,
)
_CYCLE_SLIP_RE = re.compile(
    r"Cycle slip detected on channel (\d+) at RX time\s+([0-9.]+)\s+s,\s+"
    r"for satellite (.+?), signal (\S+)",
    re.IGNORECASE,
)
_RTKPOS_ERROR_RE = re.compile(r"rtkpos error:\s*(.+point pos error.+)", re.IGNORECASE)
_PVT_RESET_RE = re.compile(
    r"PVT: Number of consecutive position solver error reached, Sent reset to observables|"
    r"Received reset observables TOW command from PVT",
    re.IGNORECASE,
)
_CLOCK_OFFSET_RE = re.compile(
    r"PVT: Sent clock offset correction to observables:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\[s\]|"
    r"Corrected new RX Time offset:\s*([+-]?\d+)\[ms\]",
    re.IGNORECASE,
)
_IDLE_RE = re.compile(r"Channel (\d+) Idle state")
_RECEIVER_TIME_RE = re.compile(
    r"Current receiver time:\s*(?:(\d+)\s*h\s*)?(?:(\d+)\s*min\s*)?(\d+)\s*s"
)
_GLOG_LINE_RE = re.compile(r"^[IWEF]\d{8}\s+\d{2}:\d{2}:\d{2}\.\d+\s+.*\]")
_GLOG_PREFIX_RE = re.compile(r"^[IWEF]\d{8}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+[^]]+\]\s*")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_GNSS_DIAGNOSTIC_RE = re.compile(
    r"(?:"
    r"chi[- ]?square|i[- ]?square|rtkpos|rtklib_pvt_residual|point\s+pos|pos\s+error|"
    r"loss\s+of\s+lock|cycle\s+slip|reset\s+observables|error\s+nv="
    r")",
    re.IGNORECASE,
)
_CONSOLE_FRAGMENT_RE = re.compile(r"^[\s\d.+\-eE()]+$")

def _parse_acquisition_metrics(text: str) -> dict[str, object]:
    key_map = {
        "test_statistics": "acq_test_statistic",
        "threshold": "acq_threshold",
        "threshold_margin": "acq_threshold_margin",
        "threshold_ratio": "acq_threshold_ratio",
        "code_phase": "acq_code_phase",
        "doppler": "acq_doppler_hz",
        "input_signal_power": "acq_input_signal_power",
    }
    metrics: dict[str, object] = {}
    for key, raw_value in _ACQUISITION_METRIC_RE.findall(text):
        parsed_key = key_map[key]
        value = float(raw_value)
        if parsed_key in {"acq_code_phase", "acq_doppler_hz"} and value.is_integer():
            metrics[parsed_key] = int(value)
        else:
            metrics[parsed_key] = value
    return metrics
