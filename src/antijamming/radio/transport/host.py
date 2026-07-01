"""
Host-side network diagnostics for the path to a USRP (MTU, NIC, sysctl).

Used at stream startup so logs correlate overflows with OS tuning (see
docs/hardware.md).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


# =============================================================================
# Address and Interface Resolution
# =============================================================================

# UHD device arguments are free-form comma-separated strings. These helpers only
# need the destination IP so the startup report can inspect the host network path.

def extract_ipv4_from_usrp_addr(usrp_addr: str) -> str | None:
    """Extract an IPv4 address from a UHD device address string."""
    m = re.search(r"addr=([\d.]+)", usrp_addr)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", usrp_addr)
    return m.group(1) if m else None


def iface_for_dest_ip(dest_ip: str) -> str | None:
    """Return the Linux egress interface selected for a destination IP."""
    try:
        r = subprocess.run(
            ["ip", "route", "get", dest_ip],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"\bdev (\S+)", r.stdout)
    return m.group(1) if m else None


def iface_mtu(iface: str) -> int | None:
    """Return the Linux MTU for an interface."""
    return _read_sys_int(Path(f"/sys/class/net/{iface}/mtu"))


def iface_link_speed_mbps(iface: str) -> int | None:
    """Return the negotiated link speed for an interface, when available."""
    speed = _read_sys_int(Path(f"/sys/class/net/{iface}/speed"))
    if speed is not None and speed > 0:
        return speed
    return None


def recommended_uhd_frame_size_for_dest_ip(dest_ip: str) -> int | None:
    """Choose the product UHD UDP frame size for a 10GbE host route."""
    iface = iface_for_dest_ip(dest_ip)
    if not iface:
        return None

    mtu = iface_mtu(iface)
    speed = iface_link_speed_mbps(iface)
    if mtu is None:
        return None
    if speed is not None and speed < 10000:
        return None

    payload_limit = max(576, int(mtu) - 28)
    return min(8000, payload_limit)


# =============================================================================
# Linux Transport Tunable Readers
# =============================================================================

# These readers are best-effort diagnostics. Missing sysfs/procfs files should
# never block streaming, especially on non-Linux development machines.

def _read_sys_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_sysctl_dotted(name: str) -> int | None:
    try:
        p = Path("/proc/sys") / name.replace(".", "/")
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


# =============================================================================
# Startup Transport Report
# =============================================================================

# Transport diagnostics are logged once per runtime start. They are deliberately
# text lines rather than structured metrics because they go straight to logs.

def collect_host_transport_report(usrp_addr: str) -> list[str]:
    """Collect startup diagnostics for the host network path to the USRP."""
    lines: list[str] = []
    lines.append("Host transport snapshot (toward USRP)")
    ip = extract_ipv4_from_usrp_addr(usrp_addr)
    if not ip:
        lines.append("  Could not parse IPv4 from usrp_addr; skip iface/MTU lookup.")
        return lines
    lines.append(f"  Parsed USRP IPv4: {ip}")
    iface = iface_for_dest_ip(ip)
    if not iface:
        lines.append("  ip route get: failed or no iface (no route to host?)")
    else:
        lines.append(f"  Egress interface: {iface}")
        # MTU and NIC rings are common causes of UHD overflows on 10GbE paths.
        mtu = iface_mtu(iface)
        if mtu is not None:
            lines.append(f"  Interface MTU: {mtu}")
            if mtu < 8028:
                lines.append(
                    "  ERROR: product runs require a 10GbE jumbo-frame path "
                    "for recv/send_frame_size=8000."
                )
        speed = iface_link_speed_mbps(iface)
        if speed is not None:
            lines.append(f"  sysfs link speed: {speed} Mb/s")
            if speed < 10000:
                lines.append("  ERROR: product runs require a 10GbE host link.")
        try:
            drv = Path(f"/sys/class/net/{iface}/device/driver").resolve().name
            lines.append(f"  Driver: {drv}")
        except OSError:
            pass
        try:
            # Ring depth is optional and driver-specific, so failures remain
            # diagnostic-only.
            out = subprocess.run(
                ["ethtool", "-g", iface],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if out.returncode == 0:
                for row in out.stdout.splitlines():
                    s = row.strip()
                    if s.startswith("RX:") or s.startswith("TX:"):
                        lines.append(f"  ethtool -g: {s}")
        except (OSError, subprocess.TimeoutExpired):
            pass

    rm = _read_sysctl_dotted("net.core.rmem_max")
    wm = _read_sysctl_dotted("net.core.wmem_max")
    rd = _read_sysctl_dotted("net.core.rmem_default")
    wd = _read_sysctl_dotted("net.core.wmem_default")
    lines.append(
        f"  sysctl: net.core.rmem_max={rm} rmem_default={rd} "
        f"wmem_max={wm} wmem_default={wd}"
    )
    lines.append(
        "  Tuning hints (Ettus/UHD): use a 10GbE jumbo-frame path, adequate "
        "rmem/wmem, and raised NIC rings."
    )
    return lines
