"""Keep native receiver dependencies on the system UHD release."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "setup.sh").read_text()


def test_setup_uses_distribution_radio_dependencies() -> None:
    packages = SOURCE.split("select_gnss_packages() {", 1)[1].split(
        "check_apt_packages_available() {", 1
    )[0]
    assert "\n    gnuradio-dev\n" in packages
    for package in ("uhd-host", "libuhd-dev", "python3-uhd"):
        assert f"\n    {package}\n" in SOURCE
    assert "apt-get install -y uhd-host libuhd-dev python3-uhd gnuradio-dev" in SOURCE
    assert "uhd_version" not in SOURCE
    assert "build_uhd_runtime" not in SOURCE
    assert "build_gnuradio_runtime" not in SOURCE


def test_gnss_configuration_clears_stale_cache_and_pins_both_libraries(
    tmp_path: Path,
) -> None:
    # Exercise the real setup function; fake only the expensive native builder.
    binary = tmp_path / "cmake"
    calls = tmp_path / "calls"
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$CALLS"\n')
    binary.chmod(0o755)
    (tmp_path / "bin").mkdir()
    checker = tmp_path / "bin/python"
    checker.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$CALLS"\n')
    checker.chmod(0o755)
    body = SOURCE.split("build_local_gnss_sdr() {", 1)[1].split(
        "\nprofile_local_gnss_sdr() {", 1
    )[0]
    prefix = tmp_path / "source prefix"
    result = subprocess.run(
        ["bash", "-c", "set -eu\nbuild_local_gnss_sdr() {" + body
         + "\nbuild_local_gnss_sdr"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "CALLS": str(calls), "UHD_INSTALL_PREFIX": str(prefix),
             "UHD_LIBRARY_DIR": str(prefix / "lib"),
             "VENV_DIR": str(tmp_path), "ROOT_DIR": str(ROOT),
             "GNSS_SRC_DIR": str(tmp_path / "src"),
             "GNSS_BUILD_DIR": str(tmp_path / "build"),
             "GNSS_INSTALL_DIR": str(tmp_path / "install")},
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    arguments = calls.read_text().splitlines()
    assert f"-DUHD_ROOT={prefix}" in arguments
    assert f"-DGNURADIO_INSTALL_PREFIX={prefix}" in arguments
    assert "-DCMAKE_IGNORE_PREFIX_PATH=/usr/local" in arguments
    assert arguments.index(str(ROOT / "tools/verify_native_stack.py")) < arguments.index("--build")
    assert "-U" in arguments
    assert "*UHD*" in arguments and "*GNURADIO*" in arguments
    assert "*GRLIMESDR*" in arguments


def test_removed_optional_limesdr_headers_do_not_poison_reconfiguration(
    tmp_path: Path,
) -> None:
    """Use the real native finder to reproduce and clear a removed SDK cache."""
    if shutil.which("cmake") is None:
        pytest.skip("CMake is required to exercise the native finder")
    source = tmp_path / "source"
    source.mkdir()
    stale = tmp_path / "removed SDK/include"
    stale.mkdir(parents=True)
    library = tmp_path / "libgnuradio-limesdr.so"
    library.touch()
    (source / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.16)\n'
        'project(StaleRadioCache NONE)\n'
        f'list(PREPEND CMAKE_MODULE_PATH "{ROOT / "gnss-sdr/cmake/Modules"}")\n'
        f'set(GNSSSDR_LIB_PATHS "{tmp_path}")\n'
        'find_package(GRLIMESDR QUIET)\n'
    )
    command = ["cmake", "-S", str(source), "-B", str(tmp_path / "build")]
    broken = subprocess.run(
        [*command, f"-DGRLIMESDR_INCLUDE_DIR={stale}",
         f"-DGRLIMESDR_LIBRARIES={library}"],
        capture_output=True, text=True, timeout=30,
    )
    assert broken.returncode != 0
    assert "limesdr/source.h" in broken.stderr
    assert "*GRLIMESDR*" in SOURCE
    recovered = subprocess.run(
        [*command, "-U", "*GRLIMESDR*"],
        capture_output=True, text=True, timeout=30,
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


@pytest.mark.parametrize("script", [
    "setup.sh", "run_realtime.sh", "run_tests.sh",
])
def test_native_setup_shell_syntax(script: str) -> None:
    result = subprocess.run(["bash", "-n", str(ROOT / script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("busy", [False, True])
def test_setup_never_rebuilds_a_mapped_receiver_library(tmp_path: Path, busy: bool) -> None:
    binary = tmp_path / "fuser"
    binary.write_text(f"#!/bin/bash\nexit {0 if busy else 1}\n")
    binary.chmod(0o755)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/libuhd.so").touch()
    body = SOURCE.split("ensure_runtime_idle() {", 1)[1].split(
        "\nensure_vendored_gnss_sdr() {", 1
    )[0]
    result = subprocess.run(
        ["bash", "-c", 'set -eu\nrun_privileged() { "$@"; }\nensure_runtime_idle() {' + body
         + "\nensure_runtime_idle"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "UHD_LIBRARY_DIR": str(tmp_path / "lib"), "GNSS_BUILD_BIN": "/not/built"},
        text=True, capture_output=True, timeout=10,
    )
    assert (result.returncode != 0) == busy
    if busy:
        assert "Stop the anti-jamming service" in result.stderr
    assert 'run_privileged fuser -s "${path}"' in body


def test_setup_checks_idle_before_package_changes() -> None:
    pipeline = SOURCE.split("\nensure_vendored_gnss_sdr\n", 1)[1]
    assert pipeline.index("ensure_runtime_idle") < pipeline.index("install_system_dependencies")
