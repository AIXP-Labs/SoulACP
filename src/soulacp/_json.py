"""Strict JSON decoding shared by untrusted input boundaries."""

from __future__ import annotations

import json
from typing import Any


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is not allowed: {key!r}")
        result[key] = value
    return result


def strict_json_loads(content: str) -> Any:
    """Decode standard JSON without silently collapsing duplicate keys."""
    return json.loads(
        content,
        object_pairs_hook=_reject_duplicate_object_keys,
        parse_constant=_reject_json_constant,
    )
