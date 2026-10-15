import re

import pytest

from anonymization_pipeline.detectors.patterns import compile_regex, fragments


def test_fragments_nested_in_any_order_and_lists_as_alternatives() -> None:
    defined = fragments(
        {"data": "${giorno}/${mese}", "giorno": r"\d{2}", "mese": ["gen", "feb"]},
        "test",
    )

    assert defined["mese"] == "gen|feb"
    assert compile_regex("${data}", defined, "test").pattern == r"\d{2}/gen|feb"
    assert compile_regex(["a", "${giorno}"], defined, "test", re.I) == re.compile(
        r"a|\d{2}", re.I
    )


@pytest.mark.parametrize(
    ("value", "defined", "message"),
    [
        ("${nessuno}", {}, r"unknown fragment \$\{nessuno\} \(defined: none\)"),
        ("${a}", {"a": "${b}", "b": "${a}"}, r"recursive fragment \$\{a\}"),
        ("(", {}, "invalid regex"),
        ("", {}, "expected a regex"),
        (12, {}, "expected a regex"),
        ([], {}, "expected a non-empty list"),
        (["a", 1], {}, "expected a non-empty list"),
    ],
)
def test_invalid_expressions_name_the_place(
    value: object, defined: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^rules.yaml, pattern 3: {message}"):
        compile_regex(value, defined, "rules.yaml, pattern 3")


def test_fragments_must_be_a_mapping() -> None:
    with pytest.raises(ValueError, match="expected a mapping"):
        fragments(["a"], "labels.yaml, fragments")
