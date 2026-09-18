"""Exercise setup's actual shell functions without packages, network or radio."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "setup.sh").read_text(encoding="utf-8")


def function(name: str, following: str) -> str:
    body = SOURCE.split(f"{name}() {{", 1)[1].split(
        f"\n{following}() {{", 1
    )[0]
    return f"{name}() {{" + body


def run_check(
    tmp_path: Path,
    *,
    flavor: str = "HG",
    product: str = "X300",
    serials: str = "serial: 35D068D",
    probe_status: int = 0,
    probe_text: str = "Device: X-Series Device",
    discover_status: int = 0,
    loader_status: int = 0,
) -> subprocess.CompletedProcess[str]:
    prefix = tmp_path / "prefix"
    binary_dir = prefix / "bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    image_dir = tmp_path / "images"
    image_dir.mkdir(exist_ok=True)
    (image_dir / "usrp_x300_fpga_HG.bit").write_bytes(b"test image, not FPGA data")
    calls = tmp_path / "calls"
    outputs = {
        "uhd_find_devices": (
            f"product: {product}\n{serials}\nfpga: {flavor}", discover_status
        ),
        "uhd_usrp_probe": (probe_text, probe_status),
        "uhd_image_loader": ("write result", loader_status),
    }
    for name, (output, status) in outputs.items():
        path = binary_dir / name
        path.write_text(
            "#!/bin/bash\n"
            f"printf '%s\\n' \"{name} $*\" >> {shlex.quote(str(calls))}\n"
            f"printf '%s\\n' {shlex.quote(output)}\nexit {status}\n"
        )
        path.chmod(0o755)
    body = function("ensure_x300_hg_image_loaded", "build_local_gnss_sdr")
    command = (
        "set -euo pipefail\n"
        f"UHD_IMAGE_DIR={shlex.quote(str(image_dir))}\n"
        f"UHD_INSTALL_PREFIX={shlex.quote(str(prefix))}\n"
        "USRP_ADDR=192.168.40.2\n"
        + body
        + "\nensure_x300_hg_image_loaded\nprintf 'READY\\n'\n"
    )
    return subprocess.run(
        ["bash", "-c", command], text=True, capture_output=True, timeout=10
    )


def test_hg_requires_real_initialization_before_ready(tmp_path: Path) -> None:
    result = run_check(tmp_path)
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 0, result.stderr
    assert "uhd_usrp_probe --args type=x300,addr=192.168.40.2,serial=35D068D" in calls
    assert "uhd_image_loader" not in calls
    assert "READY" in result.stdout


@pytest.mark.parametrize(
    "message",
    [
        "Major compat number mismatch for 0/Radio#0: Expecting 0, got 1.",
        "RuntimeError: FPGA component `0/Radio#0' is revision 1 and UHD supports revision 0.",
        "Expected FPGA compatibility number 39, but got 38",
    ],
)
def test_hg_mismatch_writes_verified_image_but_never_reports_ready(
    tmp_path: Path, message: str
) -> None:
    result = run_check(tmp_path, probe_status=1, probe_text=message)
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 2
    assert "serial=35D068D,fpga=HG,verify" in calls
    assert "--fpga-path" in calls
    assert "READY" not in result.stdout
    assert "power-cycled" in result.stderr
    assert (tmp_path / "images/.antijamming-x300-35D068D.pending").is_file()


@pytest.mark.parametrize("status", [1, 124])
def test_noncompatibility_probe_failure_never_flashes(tmp_path: Path, status: int) -> None:
    result = run_check(tmp_path, probe_status=status, probe_text="Device is busy")
    assert result.returncode == 1
    assert "uhd_image_loader" not in (tmp_path / "calls").read_text()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"product": "X310"},
        {"serials": ""},
        {"serials": "serial: 35D068D\nserial: ANOTHER"},
        {"discover_status": 1},
        {"flavor": "UNKNOWN"},
    ],
)
def test_unidentified_or_ambiguous_target_never_flashes(tmp_path: Path, kwargs: dict) -> None:
    result = run_check(tmp_path, **kwargs)
    assert result.returncode != 0
    calls = (tmp_path / "calls").read_text()
    assert "uhd_usrp_probe" not in calls and "uhd_image_loader" not in calls


def test_wrong_flavor_is_changed_even_if_compatible(tmp_path: Path) -> None:
    result = run_check(tmp_path, flavor="XG")
    assert result.returncode == 2
    assert "uhd_image_loader" in (tmp_path / "calls").read_text()


def test_failed_write_does_not_leave_success_or_pending_marker(tmp_path: Path) -> None:
    result = run_check(tmp_path, flavor="XG", loader_status=9)
    assert result.returncode == 9
    assert "READY" not in result.stdout
    assert not (tmp_path / "images/.antijamming-x300-35D068D.pending").exists()


def test_rerun_waits_for_activation_then_clears_pending_marker(tmp_path: Path) -> None:
    first = run_check(tmp_path, probe_status=1, probe_text="compat number mismatch")
    second = run_check(tmp_path, probe_status=1, probe_text="compat number mismatch")
    assert first.returncode == second.returncode == 2
    assert (tmp_path / "calls").read_text().count("uhd_image_loader") == 1
    third = run_check(tmp_path)
    assert third.returncode == 0
    assert not (tmp_path / "images/.antijamming-x300-35D068D.pending").exists()


def test_existing_image_does_not_bypass_release_downloader(tmp_path: Path) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    image = tmp_path / "usrp_x300_fpga_HG.bit"
    image.write_bytes(b"old or wrong release")
    downloader = binary_dir / "uhd_images_downloader"
    downloader.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\"\n")
    downloader.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + function("ensure_uhd_images", "host_cidr_for_usrp_addr")
         + "\nensure_uhd_images"],
        env={**os.environ, "UHD_INSTALL_PREFIX": str(tmp_path), "UHD_IMAGE_DIR": str(tmp_path)},
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--refetch" in result.stdout
    assert "^x3xx_x300_fpga_default$" in result.stdout


def test_setup_checks_images_before_device_discovery() -> None:
    pipeline = SOURCE.split('\nensure_vendored_gnss_sdr\n', 1)[1]
    assert pipeline.index("\nensure_uhd_images\n") < pipeline.index(
        "\nresolve_x300_host_link\n"
    )
