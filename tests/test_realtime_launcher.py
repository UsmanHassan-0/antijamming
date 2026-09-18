from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from antijamming.config.paths import REPO_ROOT


@pytest.fixture
def launcher_tree(tmp_path: Path) -> Path:
    """Run the actual shell and JSONC reader without UHD, a GUI or live processes.

    Only external boundaries are replaced: the GUI entry point, route lookup,
    process discovery and the diagnostic sidecar. Child stubs exit
    immediately, including when the old launcher abandons its normal wait path.
    """
    shutil.copy2(REPO_ROOT / "run_realtime.sh", tmp_path / "run_realtime.sh")
    package = tmp_path / "src/antijamming"
    (package / "app").mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "app/__init__.py").touch()
    shutil.copy2(REPO_ROOT / "src/antijamming/jsonc.py", package / "jsonc.py")
    (package / "app/main.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path('app_started.json').write_text(json.dumps(sys.argv[1:]))\n"
        "Path('app_environment.json').write_text(json.dumps({\n"
        "    key: os.environ.get(key) for key in ('MPLBACKEND', 'QT_QPA_PLATFORM')\n"
        "}))\n"
        "raise SystemExit(int(os.environ['TEST_APP_STATUS']))\n",
        encoding="utf-8",
    )
    (tmp_path / ".aj/bin").mkdir(parents=True)
    (tmp_path / ".aj/bin/python").symlink_to(sys.executable)
    (tmp_path / "configs/antijamming").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/run_realtime_sidecar.sh").write_text(
        'printf "%s\\n" "${IFACE}" > "${ROOT}/sidecar_iface.txt"\n',
        encoding="utf-8",
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "ip").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" > route_request.txt\n'
        '[[ "$*" == "route get 192.168.40.2" ]] || exit 91\n'
        'printf "%s\\n" "192.168.40.2 dev test_eth src 192.168.40.1"\n',
        encoding="utf-8",
    )
    (commands / "ps").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for command in commands.iterdir():
        command.chmod(0o755)
    return tmp_path


def _run_launcher(
    root: Path,
    *,
    logging: bool = True,
    address: str = "addr=192.168.40.2",
    interface: str = "",
    app_status: int = 0,
    environment: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    (root / "configs/antijamming/x300_realtime.jsonc").write_text(
        "// Exercise the real comment-aware reader.\n"
        + json.dumps({"logging_enabled": logging, "usrp_addr": address}),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{root / 'commands'}:{os.environ['PATH']}",
        "PYTHONPATH": "",
        "QT_QPA_PLATFORM": "offscreen",
        "ANTIJAM_SIDECAR_IFACE": interface,
        "TEST_APP_STATUS": str(app_status),
    }
    for key, value in (environment or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["bash", str(root / "run_realtime.sh"), "--auto-stop-after-s", "3"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("app_status", [0, 7])
def test_launcher_resolves_interface_and_returns_app_status(
    launcher_tree: Path, app_status: int
) -> None:
    result = _run_launcher(launcher_tree, app_status=app_status)

    assert result.returncode == app_status, result.stderr
    assert (launcher_tree / "route_request.txt").read_text().strip() == (
        "route get 192.168.40.2"
    )
    assert (launcher_tree / "sidecar_iface.txt").read_text().strip() == "test_eth"
    assert json.loads((launcher_tree / "app_started.json").read_text()) == [
        "--auto-stop-after-s",
        "3",
    ]


@pytest.mark.parametrize("failure", ["missing_address", "python_exception"])
def test_launcher_lookup_failure_starts_no_children(
    launcher_tree: Path, failure: str
) -> None:
    if failure == "python_exception":
        # Inject a failing helper only in the disposable launcher copy.
        launcher = launcher_tree / "run_realtime.sh"
        source = launcher.read_text(encoding="utf-8")
        assert source.count("import re\n") == 1
        launcher.write_text(
            source.replace(
                "import re\n", "raise RuntimeError('injected address lookup failure')\n"
            ),
            encoding="utf-8",
        )
    result = _run_launcher(launcher_tree, address="serial=unresolved")

    assert result.returncode != 0
    if failure == "python_exception":
        assert "injected address lookup failure" in result.stderr
    assert not (launcher_tree / "app_started.json").exists()
    assert not (launcher_tree / "sidecar_iface.txt").exists()
    assert not (launcher_tree / "route_request.txt").exists()


@pytest.mark.parametrize("backend", [None, "svg"])
def test_launcher_does_not_choose_matplotlib_backend(
    launcher_tree: Path, backend: str | None
) -> None:
    result = _run_launcher(launcher_tree, environment={"MPLBACKEND": backend})

    assert result.returncode == 0, result.stderr
    environment = json.loads((launcher_tree / "app_environment.json").read_text())
    assert environment["MPLBACKEND"] == backend
    assert environment["QT_QPA_PLATFORM"] == "offscreen"


@pytest.mark.parametrize("platform", ["xcb", "wayland", None])
@pytest.mark.parametrize("app_status", [0, 7])
def test_launcher_leaves_display_validation_to_application(
    launcher_tree: Path, platform: str | None, app_status: int
) -> None:
    # No real GUI is launched: the child decides whether its environment works.
    result = _run_launcher(
        launcher_tree,
        app_status=app_status,
        environment={
            "QT_QPA_PLATFORM": platform,
            "DISPLAY": None,
            "WAYLAND_DISPLAY": None,
        },
    )

    assert result.returncode == app_status, result.stderr
    assert (launcher_tree / "app_started.json").exists()
    assert (launcher_tree / "sidecar_iface.txt").exists()
    for message in ("Cannot open", "computer's desktop", "window does not appear"):
        assert message not in result.stdout + result.stderr
    environment = json.loads((launcher_tree / "app_environment.json").read_text())
    assert environment["QT_QPA_PLATFORM"] == (platform or "xcb")


@pytest.mark.parametrize(
    ("platform", "display", "wayland_display", "expected_platform"),
    [
        ("offscreen", None, None, "offscreen"),
        ("minimal", None, None, "minimal"),
        ("xcb", ":77", None, "xcb"),
        ("wayland", None, "wayland-test", "wayland"),
        (None, ":77", None, "xcb"),
        (None, None, "wayland-test", "wayland"),
    ],
)
def test_launcher_preserves_display_selection_without_printing_internals(
    launcher_tree: Path,
    platform: str | None,
    display: str | None,
    wayland_display: str | None,
    expected_platform: str,
) -> None:
    # The GUI is a stub: this verifies environment handoff, not display access.
    result = _run_launcher(
        launcher_tree,
        environment={
            "QT_QPA_PLATFORM": platform,
            "DISPLAY": display,
            "WAYLAND_DISPLAY": wayland_display,
        },
    )

    assert result.returncode == 0, result.stderr
    assert (launcher_tree / "app_started.json").exists()
    environment = json.loads((launcher_tree / "app_environment.json").read_text())
    assert environment["QT_QPA_PLATFORM"] == expected_platform
    for detail in (
        "Qt platform:", "Display target:", "QT_QPA_PLATFORM", "DISPLAY",
        "window does not appear",
    ):
        assert detail not in result.stdout + result.stderr


def test_gui_display_confirmation_is_logged_not_printed() -> None:
    # This source-contract check covers our entry point, not Qt's own messages.
    source = (REPO_ROOT / "src/antijamming/app/main.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    gui_function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_gui"
    )
    calls = [node for node in ast.walk(gui_function) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(call.func, ast.Name) and call.func.id == "print" for call in calls
    )
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "info"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "GUI window shown: platform=%s geometry=%s"
        for call in calls
    )


def test_launcher_explicit_interface_bypasses_lookup(launcher_tree: Path) -> None:
    result = _run_launcher(
        launcher_tree, address="serial=unresolved", interface="explicit_eth"
    )

    assert result.returncode == 0, result.stderr
    assert (launcher_tree / "app_started.json").exists()
    assert (launcher_tree / "sidecar_iface.txt").read_text().strip() == "explicit_eth"
    assert not (launcher_tree / "route_request.txt").exists()


@pytest.mark.parametrize("logging", [False, True])
def test_launcher_skips_lookup_when_sidecar_is_not_used(
    launcher_tree: Path, logging: bool
) -> None:
    if logging:
        (launcher_tree / "tools/run_realtime_sidecar.sh").unlink()
    result = _run_launcher(launcher_tree, logging=logging, address="serial=unresolved")

    assert result.returncode == 0, result.stderr
    assert (launcher_tree / "app_started.json").exists()
    assert not (launcher_tree / "sidecar_iface.txt").exists()
    assert not (launcher_tree / "route_request.txt").exists()
