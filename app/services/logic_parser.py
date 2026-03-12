from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_invalid_json_escapes(payload: str) -> str:
    # Make invalid escapes literal (e.g. \u54b -> \\u54b, \x -> \\x),
    # while preserving valid JSON escapes.
    out: list[str] = []
    i = 0
    n = len(payload)
    valid_simple = {'"', "\\", "/", "b", "f", "n", "r", "t"}
    hex_chars = set("0123456789abcdefABCDEF")

    while i < n:
        ch = payload[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        if i + 1 >= n:
            out.append("\\\\")
            i += 1
            continue

        nxt = payload[i + 1]
        if nxt in valid_simple:
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        if nxt == "u":
            if i + 6 <= n and all(c in hex_chars for c in payload[i + 2 : i + 6]):
                out.append(payload[i : i + 6])
                i += 6
            else:
                out.append("\\\\u")
                i += 2
            continue

        out.append("\\\\")
        i += 1

    return "".join(out)


def _decode_escaped_text(value: str) -> str:
    # Convert escaped unicode sequences back to real characters for storage.
    if "\\u" not in value and "\\U" not in value:
        return value
    try:
        decoded = value.encode("utf-8").decode("unicode_escape")
        decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16")
        return decoded
    except Exception:
        # Fallback: decode only valid \uXXXX segments.
        def repl(match: re.Match[str]) -> str:
            return chr(int(match.group(1), 16))

        return re.sub(r"\\u([0-9a-fA-F]{4})", repl, value)


def _normalize_text_fields(item: Any) -> Any:
    if isinstance(item, str):
        return _decode_escaped_text(item)
    if isinstance(item, list):
        return [_normalize_text_fields(v) for v in item]
    if isinstance(item, dict):
        return {k: _normalize_text_fields(v) for k, v in item.items()}
    return item


def parse_json_array(text: str) -> list[dict[str, Any]]:
    s = text.strip()
    if not s:
        raise ValueError("empty response")
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("json array not found")
    payload = s[start : end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        sanitized = _sanitize_invalid_json_escapes(payload)
        try:
            data = json.loads(sanitized)
            logger.warning("logic_parser recovered from invalid JSON escapes: %s", exc)
        except json.JSONDecodeError:
            raise
    if not isinstance(data, list):
        raise ValueError("parsed payload is not a list")
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(_normalize_text_fields(item))
    return out
