"""Regular expressions of the detector files (``rules.yaml``, ``labels.yaml``).

The same syntax in every file: ``${name}`` is replaced with the fragment ``name`` of the file
(fragments can use other fragments, in any order), and an expression or a fragment written as a
list is an alternation of its items, joined with "|" in the given order.
"""

import re
from typing import Any

_REFERENCE = re.compile(r"\$\{(\w+)\}")


def source(value: Any, where: str) -> str:
    """Text of an expression written as a string or as a list of alternatives.

    Raises:
        ValueError: If the expression is empty or not text.
    """
    if isinstance(value, list):
        if not value or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"{where}: expected a non-empty list of quoted regexes")
        return "|".join(value)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a regex (quoted string), found {value!r}")
    return value


def fragments(raw: Any, where: str) -> dict[str, str]:
    """The ``fragments`` section: name -> expression, references not yet replaced.

    Raises:
        ValueError: If the section is not a mapping or a fragment is not an expression.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a mapping name -> regex")
    return {str(name): source(value, f"{where}.{name}") for name, value in raw.items()}


def expand(
    text: str, defined: dict[str, str], where: str, chain: tuple[str, ...] = ()
) -> str:
    """Replace every ``${name}`` with its fragment, also nested.

    Raises:
        ValueError: On an unknown or recursive fragment.
    """

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in defined:
            known = ", ".join(defined) or "none"
            raise ValueError(
                f"{where}: unknown fragment ${{{name}}} (defined: {known})"
            )
        if name in chain:
            raise ValueError(f"{where}: recursive fragment ${{{name}}}")
        return expand(defined[name], defined, where, (*chain, name))

    return _REFERENCE.sub(one, text)


def compile_regex(
    value: Any, defined: dict[str, str], where: str, flags: int = 0
) -> re.Pattern[str]:
    """Compile an expression of a detector file (string or list of alternatives).

    Raises:
        ValueError: If the expression is not text, uses an unknown fragment or is invalid.
    """
    text = expand(source(value, where), defined, where)
    try:
        return re.compile(text, flags)
    except re.error as exc:
        raise ValueError(f"{where}: invalid regex ({exc})") from None
