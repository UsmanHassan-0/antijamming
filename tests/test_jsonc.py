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
