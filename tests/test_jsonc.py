from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from antijamming import jsonc
from antijamming.config import DEFAULT_RUNTIME_CONFIG_PATH, REPO_ROOT, load_stream_config_file


def test_comments_preserve_source_offsets_and_parse_at_eof() -> None:
    source = '// hello\r\n{ /* header */ "rate": 4, // samples\n"on": true } // EOF'
    assert jsonc.loads(source) == {"rate": 4, "on": True}
    masked = jsonc.mask_comments(source)
    assert len(masked) == len(source)
    assert [i for i, c in enumerate(source) if c in "\r\n"] == [
        i for i, c in enumerate(masked) if c in "\r\n"
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/a/*literal*/", "//", "/*", "*/", "# heading",
        'escaped quote: " // still a string', 'backslashes: \\\\ " /* still a string',
        "line\ncarriage\rtab\t", "Unicode: π 😃", "ends in backslash \\",
    ],
)
def test_comment_markers_and_escapes_in_strings_are_unchanged(value: str) -> None:
    source = json.dumps({"value": value}, ensure_ascii=False)
    assert jsonc.mask_comments(source) == source
    assert jsonc.loads("/* start */" + source + "// end") == {"value": value}


@pytest.mark.parametrize(
    "source",
    [
        '{"a": 1,}', "{'a': 1}", '{a: 1}', '{"a": 1/*gap*/2}',
        '{"a": tr/*gap*/ue}', '{"a": 1} /* unterminated',
        '{"a": "unterminated // not a comment}', '{"a": "bad\\q"}',
        '{"a": "raw\nnewline"}', '# comment\n{"a": 1}',
    ],
)
def test_comments_do_not_relax_other_json_syntax(source: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        jsonc.loads(source)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_non_finite_numbers_are_rejected_even_in_nested_metadata(value: str) -> None:
    with pytest.raises(ValueError, match="non-finite JSON number"):
        jsonc.loads('{"_metadata": [/* comment */ {"bad": ' + value + "}]}")


@pytest.mark.parametrize(
    "source",
    ['{"a": 1, /* same key */ "a": 2}', '{"outer": {"a": 1, // same\n"a": 2}}'],
)
def test_duplicate_keys_are_still_rejected(source: str) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key 'a'"):
        jsonc.loads(source)


def test_runtime_keeps_previous_huge_integer_validation(tmp_path: Path) -> None:
    profile = jsonc.load(DEFAULT_RUNTIME_CONFIG_PATH)
    profile["_metadata"] = {"too_large_for_runtime_float": 10 ** 400}
    path = tmp_path / "huge_integer.jsonc"
    path.write_text("// Same runtime numeric boundary as before JSONC\n" + json.dumps(profile))
    with pytest.raises(ValueError, match="must be finite"):
        load_stream_config_file(path)


def test_errors_keep_original_filename_line_and_column(tmp_path: Path) -> None:
    source = '{\n/* two\nlines */\n"a": ]\n}'
    path = tmp_path / "bad.jsonc"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as error:
        jsonc.loads(source)
    assert (error.value.lineno, error.value.colno) == (4, 6)
    with pytest.raises(ValueError, match=r"bad.jsonc.*line 4 column 6"):
        jsonc.load(path)


@pytest.mark.parametrize("script", ["run_realtime.sh", "tools/run_realtime_sidecar.sh"])
def test_real_shell_profile_readers_accept_comments_without_site_packages(
    tmp_path: Path, script: str,
) -> None:
    path = tmp_path / "configs/antijamming/x300_realtime.jsonc"
    path.parent.mkdir(parents=True)
    path.write_text(
        '// profile\n{"logging_enabled": false, /* gate */ '
        '"usrp_addr": "addr=192.168.40.2"}\n', encoding="utf-8",
    )
    source = (REPO_ROOT / script).read_text(encoding="utf-8")
    blocks = [
        block for block in re.findall(r"<<'PY'\n(.*?)\nPY", source, flags=re.DOTALL)
        if "from antijamming.jsonc import load" in block
    ]
    assert len(blocks) == (2 if script == "run_realtime.sh" else 1)
    for block in blocks:
        result = subprocess.run(
            [sys.executable, "-S", "-c", block], cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
            capture_output=True, text=True, timeout=10, check=True,
        )
        assert result.stdout.strip() == ("0" if "logging_enabled" in block else "192.168.40.2")


def _setup_profile_writer() -> str:
    source = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
    function = source.split("persist_runtime_usrp_addr() {", 1)[1]
    return function.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def test_setup_writer_preserves_comments_and_edits_only_the_real_address(tmp_path: Path) -> None:
    source = (
        '{\n/* Example only:\n"usrp_addr": "addr=1.2.3.4"\n*/\n'
        '"usrp_addr": "addr=192.168.40.2,recv_frame_size=8000", // actual\n'
        '"gain_db": 45.0\n}\n'
    )
    path = tmp_path / "profile.jsonc"
    path.write_text(source, encoding="utf-8")
    path.chmod(0o640)
    environment = {
        **os.environ, "PYTHONPATH": str(REPO_ROOT / "src"),
        "ANTIJAMMING_RUNTIME_CONFIG": str(path),
        "ANTIJAMMING_DETECTED_USRP_ADDR": "192.168.30.2",
    }
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-S", "-c", _setup_profile_writer()], env=environment,
            capture_output=True, text=True, timeout=10, check=True,
        )
        assert path.read_text() == source.replace("addr=192.168.40.2", "addr=192.168.30.2")
        assert path.stat().st_mode & 0o777 == 0o640
        assert sorted(p.name for p in tmp_path.iterdir()) == ["profile.jsonc"]


def test_setup_writer_does_not_mutate_an_invalid_commented_profile(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonc"
    source = '{"usrp_addr": "addr=1.2.3.4", /* invalid */ "gain_db": NaN}'
    path.write_text(source)
    result = subprocess.run(
        [sys.executable, "-S", "-c", _setup_profile_writer()],
        env={
            **os.environ, "PYTHONPATH": str(REPO_ROOT / "src"),
            "ANTIJAMMING_RUNTIME_CONFIG": str(path),
            "ANTIJAMMING_DETECTED_USRP_ADDR": "192.168.30.2",
        }, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0 and "non-finite JSON number" in result.stderr
    assert path.read_text() == source
    assert sorted(p.name for p in tmp_path.iterdir()) == ["invalid.jsonc"]
