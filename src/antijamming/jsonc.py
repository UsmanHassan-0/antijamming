"""Standard-library-only JSONC reader shared with shell entry points.

Allow // and /* */ comments, without relaxing JSON value syntax, duplicate-key
checks or finite-number requirements. Masking retains source offsets for error
locations and setup's comment-preserving address edit.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def mask_comments(source: str) -> str:
    """Replace comments with spaces; preserve strings, length and newlines."""
    chars = list(source)
    index = 0
    while index < len(source):
        if source[index] == '"':
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            # JSON decoding still checks escapes, control characters and EOF.
            continue
        if source.startswith("//", index):
            end = index + 2
            while end < len(source) and source[end] not in "\r\n":
                end += 1
        elif source.startswith("/*", index):
            close = source.find("*/", index + 2)
            if close < 0:
                raise json.JSONDecodeError("Unterminated block comment", source, index)
            end = close + 2
        else:
            index += 1
            continue
        for position in range(index, end):
            if source[position] not in "\r\n":
                chars[position] = " "
        index = end
    return "".join(chars)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite JSON number {value!r}")
    return result


def loads(source: str) -> Any:
    """Decode JSONC; trailing commas and other non-JSON syntax remain errors."""
    return json.loads(
        mask_comments(source),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )


def load(path: Path | str) -> Any:
    """Read UTF-8 JSONC, retaining the filename and parser error location."""
    path = Path(path)
    try:
        return loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Invalid runtime config {path}: {exc}") from exc
