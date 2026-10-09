"""Parsing of the model JSON answers and schemas for constrained decoding.

The model output is not trusted: only entries with the expected shape are accepted, without
duplicates. An answer cut by the token limit is not valid JSON: its complete entries are recovered.
"""

import json
import re
from typing import Any

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?(.*?)\n?\s*```\s*$", re.S)
# a complete entry {"text": "...", "category": "..."} (also with the fields in reverse order)
_ENTRY = re.compile(
    r'\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"category"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    r'|\{\s*"category"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
)


def entity_schema(categories: list[str]) -> dict[str, Any]:
    """JSON schema of a detection answer, limited to ``categories``."""
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "category": {"type": "string", "enum": list(categories)},
                    },
                    "required": ["text", "category"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entities"],
        "additionalProperties": False,
    }


GROUPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "integer"}},
        }
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def strip_fence(text: str) -> str:
    """Remove a ```...``` block that wraps the whole answer."""
    match = _FENCE.match(text)
    return match.group(1) if match else text


def parse_json(text: str) -> Any | None:
    """JSON value of an answer (also inside a fence or surrounded by text); None if not JSON."""
    t = strip_fence(text).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(t[i : j + 1])
        except json.JSONDecodeError:
            pass
    return None


def _string(raw: str) -> str | None:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return None


def parse_entities(text: str) -> list[dict[str, str]] | None:
    """``[{"text", "category"}]`` without duplicates; None if the answer cannot be parsed."""
    data = parse_json(text)
    if isinstance(data, dict):
        items = data.get("entities")
    elif isinstance(data, list):
        items = data
    else:
        items = None
        recovered = []
        for match in _ENTRY.finditer(text):
            if match.group(1) is not None:
                value, category = match.group(1), match.group(2)
            else:
                value, category = match.group(4), match.group(3)
            value, category = _string(value), _string(category)
            if value is not None and category is not None:
                recovered.append({"text": value, "category": category})
        if recovered:
            items = recovered
    if not isinstance(items, list):
        return None
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value, category = item.get("text"), item.get("category")
        if not (
            isinstance(value, str)
            and isinstance(category, str)
            and value.strip()
            and category.strip()
        ):
            continue
        key = (value.strip(), category.strip().upper())
        if key not in seen:
            seen.add(key)
            out.append({"text": key[0], "category": key[1]})
    return out


def parse_groups(text: str) -> list[list[int]] | None:
    """``[[1, 4], [2]]``; None if the answer cannot be parsed."""
    data = parse_json(text)
    items = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [
        [x for x in group if isinstance(x, int) and not isinstance(x, bool)]
        for group in items
        if isinstance(group, list)
    ]
