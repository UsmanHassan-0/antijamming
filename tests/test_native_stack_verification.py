"""Negative controls for setup's actual library/header closure validator."""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/verify_native_stack.py"
SPEC = importlib.util.spec_from_file_location("verify_native_stack", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    root = tmp_path / "source runtime"
    (root / "lib").mkdir(parents=True)
    (root / "include").mkdir()
    for name in ("libuhd.so.4.6.0", "libgnuradio-uhd.so.3.10.9",
                 "libgnuradio-runtime.so.3.10.9"):
        (root / "lib" / name).touch()
    (root / "lib/libuhd.so").symlink_to("libuhd.so.4.6.0")
    return root


def closure(prefix: Path) -> str:
    return "\n".join(
        f"\t{path.name} => {path} (0x1234)"
        for path in sorted((prefix / "lib").iterdir())
        if path.name != "libuhd.so"
    )


def test_matching_closure_with_spaces(prefix: Path) -> None:
    assert len(verifier.verify_ldd(closure(prefix), prefix)) == 3


@pytest.mark.parametrize("different_only", [True, False])
def test_different_and_mixed_uhd_rejected(prefix: Path, different_only: bool) -> None:
    output = closure(prefix)
    different = "libuhd.so.4.9.0 => /usr/lib/libuhd.so.4.9.0 (0x5678)"
    output = output.replace("libuhd.so.4.6.0", "libuhd.so.4.9.0") if different_only else output + "\n" + different
    with pytest.raises(ValueError, match="Wrong UHD ABI"):
        verifier.verify_ldd(output, prefix)


@pytest.mark.parametrize("abi", ["4.6.0", "4.9.0", "5.0.0"])
@pytest.mark.parametrize("library_directory", ["lib", "lib/aarch64-linux-gnu", "lib/x86_64-linux-gnu"])
def test_installed_library_selects_version_not_a_release_pin(
    tmp_path: Path, abi: str, library_directory: str,
) -> None:
    """Synthetic ABIs prove selection logic, not support for future hardware."""
    prefix = tmp_path / "system"
    library_dir = prefix / library_directory
    library_dir.mkdir(parents=True)
    library = library_dir / f"libuhd.so.{abi}"
    library.touch()
    (library_dir / "libuhd.so").symlink_to(library.name)
    runtime = library_dir / "libgnuradio-runtime.so.3.10.9"
    radio = library_dir / "libgnuradio-uhd.so.3.10.9"
    runtime.touch()
    radio.touch()
    output = "\n".join(
        f"{path.name} => {path} (0x1234)" for path in (library, runtime, radio)
    )
    assert verifier.system_uhd_library(prefix) == library
    assert verifier.verify_ldd(output, prefix)[library.name] == library


@pytest.mark.parametrize("output", ["", "statically linked", "libfoo.so => not found"])
def test_missing_closure_not_a_pass(prefix: Path, output: str) -> None:
    with pytest.raises(ValueError):
        verifier.verify_ldd(output, prefix)


def test_transitive_gnuradio_escape_rejected(prefix: Path, tmp_path: Path) -> None:
    outside = tmp_path / "libgnuradio-filter.so.3.10.9"
    outside.touch()
    output = closure(prefix) + f"\n{outside.name} => {outside} (0x4567)"
    with pytest.raises(ValueError, match="escaped"):
        verifier.verify_ldd(output, prefix)


def test_symlink_escape_rejected(prefix: Path, tmp_path: Path) -> None:
    outside = tmp_path / "system-library"
    outside.touch()
    link = prefix / "lib/libgnuradio-filter.so.3.10.9"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="escaped"):
        verifier.verify_ldd(closure(prefix), prefix)


def cache_text(prefix: Path) -> str:
    return (
        f"UHD_INCLUDE_DIRS:PATH={prefix}/include\n"
        f"UHD_LIBRARIES:FILEPATH={prefix}/lib/libuhd.so.4.6.0\n"
        f"GNURADIO_RUNTIME_INCLUDE_DIRS:PATH={prefix}/include\n"
        f"GNURADIO_RUNTIME_LIBRARIES_gnuradio-runtime:FILEPATH={prefix}/lib/libgnuradio-runtime.so.3.10.9\n"
    )


def test_selected_headers_and_libraries(prefix: Path, tmp_path: Path) -> None:
    cache = tmp_path / "CMakeCache.txt"
    cache.write_text(cache_text(prefix))
    verifier.verify_cache(cache, prefix)


def test_distribution_support_library_is_not_a_radio_escape(
    prefix: Path, tmp_path: Path,
) -> None:
    cache = tmp_path / "CMakeCache.txt"
    cache.write_text(cache_text(prefix)
                     + "GNURADIO_ANALOG_LIBRARIES_volk:FILEPATH=/usr/lib/libvolk.so\n")
    verifier.verify_cache(cache, prefix)


def test_unparseable_native_line_is_not_silently_ignored(prefix: Path) -> None:
    with pytest.raises(ValueError, match="Unrecognized"):
        verifier.verify_ldd(closure(prefix) + "\nlibgnuradio-filter.so => ???", prefix)


@pytest.mark.parametrize("bad", ["empty", "header", "optional_library"])
def test_cache_missing_or_system_selection_rejected(
    prefix: Path, tmp_path: Path, bad: str,
) -> None:
    text = cache_text(prefix)
    if bad == "empty":
        text = "UHD_ROOT:PATH=/requested/but/not/selected\n"
    elif bad == "header":
        text = text.replace(f"UHD_INCLUDE_DIRS:PATH={prefix}/include", "UHD_INCLUDE_DIRS:PATH=/usr/include")
    else:
        outside = tmp_path / "libgnuradio-iio.so"
        outside.touch()
        text += f"GNURADIO_IIO_LIBRARIES:FILEPATH={outside}\n"
    cache = tmp_path / "CMakeCache.txt"
    cache.write_text(text)
    with pytest.raises(ValueError):
        verifier.verify_cache(cache, prefix)
