#!/usr/bin/env python3
"""Reject stale UHD/GNU Radio searches and mixed native runtime dependencies."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import re
import subprocess


def require_prefix(path: str | Path, prefix: Path) -> Path:
    resolved = Path(path).resolve(strict=True)
    prefix = prefix.resolve(strict=True)
    if not resolved.is_relative_to(prefix) or resolved.is_relative_to(prefix / "local"):
        raise ValueError(f"Native dependency escaped {prefix}: {resolved}")
    return resolved


def system_uhd_library(prefix: Path) -> Path:
    """Use the development package's selected link, not a hard-coded release."""
    links = [prefix / "lib/libuhd.so", *prefix.glob("lib/*/libuhd.so")]
    selected = {require_prefix(path, prefix) for path in links if path.is_file()}
    if len(selected) != 1:
        raise ValueError(f"Expected one system UHD development library, found {selected}")
    library = selected.pop()
    if not re.fullmatch(r"libuhd\.so\.\d+\.\d+\.\d+", library.name):
        raise ValueError(f"Unrecognized system UHD library ABI: {library}")
    return library


def verify_cache(cache: Path, prefix: Path) -> None:
    """Check actual CMake header/library search results, not requested hints."""
    checked = set()
    for line in cache.read_text().splitlines():
        match = re.match(r"([^:#/][^:]*):(?:FILEPATH|PATH|STRING)=(.*)$", line)
        if not match:
            continue
        name, value = match.groups()
        is_uhd = name in {"UHD_INCLUDE_DIRS", "UHD_LIBRARIES"}
        is_gr = name.startswith("GNURADIO_") and (
            "INCLUDE_DIRS" in name or "_LIBRARIES_gnuradio-" in name
            or name == "GNURADIO_IIO_LIBRARIES"
        )
        if not (is_uhd or is_gr):
            continue
        if not value or value.endswith("-NOTFOUND"):
            continue
        for path in value.split(";"):
            resolved = require_prefix(path, prefix)
            if name == "UHD_LIBRARIES" and resolved != system_uhd_library(prefix):
                raise ValueError(f"CMake selected a different UHD library: {resolved}")
        checked.add(name)
    required = {
        "UHD_INCLUDE_DIRS", "UHD_LIBRARIES", "GNURADIO_RUNTIME_INCLUDE_DIRS",
        "GNURADIO_RUNTIME_LIBRARIES_gnuradio-runtime",
    }
    if not required <= checked:
        raise ValueError(f"Missing native CMake evidence: {sorted(required - checked)}")


def verify_ldd(output: str, prefix: Path) -> dict[str, Path]:
    """Inspect the complete ldd closure, including gr-uhd's transitive UHD."""
    if "not found" in output:
        raise ValueError(f"Unresolved shared libraries:\n{output}")
    native = {}
    expected_uhd = system_uhd_library(prefix)
    for line in output.splitlines():
        match = re.match(r"\s*(lib(?:uhd|gnuradio-[^\s]+)\.so[^\s]*)\s+=>\s+(.+?)\s+\(0x[0-9a-fA-F]+\)", line)
        if match:
            name, path = match.groups()
            if name.startswith("libuhd.so") and name != expected_uhd.name:
                raise ValueError(f"Wrong UHD ABI loaded: {name}")
            native[name] = require_prefix(path, prefix)
        elif line.strip().startswith(("libuhd.so", "libgnuradio-")):
            raise ValueError(f"Unrecognized native dependency line: {line}")
    if expected_uhd.name not in native or native[expected_uhd.name] != expected_uhd:
        raise ValueError("Missing matching system UHD dependency evidence")
    for component in ("runtime", "uhd"):
        if not any(name.startswith(f"libgnuradio-{component}.so.") for name in native):
            raise ValueError(f"Missing GNU Radio {component} dependency evidence")
    return native


def verify_python_stack(prefix: Path) -> None:
    """Import real adjacent bindings together and inspect Linux's loaded maps."""
    for name in ("uhd", "gnuradio.gr", "gnuradio.uhd", "gnuradio.filter", "pmt"):
        module = importlib.import_module(name)
        require_prefix(module.__file__, prefix)
    maps = Path("/proc/self/maps").read_text()
    uhd_paths = set()
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        path = fields[5]
        name = Path(path).name
        if name.startswith(("libuhd.so", "libgnuradio-")):
            require_prefix(path, prefix)
        if name.startswith("libuhd.so"):
            uhd_paths.add(Path(path).resolve())
    if uhd_paths != {system_uhd_library(prefix)}:
        raise ValueError(f"Python loaded an unexpected UHD set: {sorted(map(str, uhd_paths))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--python-only", action="store_true")
    args = parser.parse_args()
    try:
        if not args.cache and not args.binary and not args.python_only:
            parser.error("At least --cache, --binary or --python-only is required")
        if args.cache:
            verify_cache(args.cache, args.prefix)
        libraries = {}
        if args.binary:
            result = subprocess.run(["ldd", str(args.binary)], check=True,
                                    capture_output=True, text=True, timeout=30)
            libraries = verify_ldd(result.stdout, args.prefix)
        if args.binary or args.python_only:
            verify_python_stack(args.prefix)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Native stack verification failed: {exc}") from exc
    for name, path in sorted(libraries.items()):
        print(f"[native-stack] {name} => {path}")
    print(f"[native-stack] Requested checks passed for {system_uhd_library(args.prefix)}.")


if __name__ == "__main__":
    main()
