from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_SCRIPT = REPO_ROOT / "tools" / "uhd_runtime_env.sh"


def _fake_uhd_prefix(tmp_path: Path, version: str) -> Path:
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    prefix = tmp_path / "uhd-prefix"
    module_dir = prefix / "lib" / f"python{python_version}" / "site-packages" / "uhd"
    module_dir.mkdir(parents=True)
    (prefix / "lib" / "libuhd.so.4.10.0").touch()
    binding = module_dir / "libpyuhd.fake.so"
    binding.touch()
    (module_dir / "__init__.py").write_text(
        "from types import SimpleNamespace\n"
        f"libpyuhd = SimpleNamespace(__file__={str(binding)!r})\n"
        f"def get_version_string(): return {version!r}\n",
        encoding="utf-8",
    )
    return prefix


def _activate(prefix: Path) -> subprocess.CompletedProcess[str]:
    command = (
        f"set -e; source {ENV_SCRIPT!s}; "
        f"antijamming_activate_uhd_runtime {sys.executable!s}; "
        "printf 'ACTIVE_PREFIX=%s\\n' \"${ANTIJAMMING_UHD_PREFIX}\""
    )
    env = os.environ.copy()
    env["ANTIJAMMING_UHD_PREFIX"] = str(prefix)
    env.pop("PYTHONPATH", None)
    env.pop("LD_LIBRARY_PATH", None)
    return subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_uhd_runtime_activation_accepts_only_the_pinned_prefix(tmp_path: Path) -> None:
    prefix = _fake_uhd_prefix(tmp_path, "4.10.0-test")

    result = _activate(prefix)

    assert result.returncode == 0, result.stderr
    assert "[uhd-runtime] version=4.10.0-test" in result.stdout
    assert f"ACTIVE_PREFIX={prefix}" in result.stdout


def test_uhd_runtime_activation_rejects_wrong_version(tmp_path: Path) -> None:
    prefix = _fake_uhd_prefix(tmp_path, "4.6.0-test")

    result = _activate(prefix)

    assert result.returncode != 0
    assert "Wrong UHD runtime selected: 4.6.0-test" in result.stderr


def test_uhd_runtime_activation_rejects_missing_prefix(tmp_path: Path) -> None:
    result = _activate(tmp_path / "missing")

    assert result.returncode != 0
    assert "Pinned UHD runtime prefix is missing" in result.stderr
