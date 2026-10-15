"""Tolerant search of ground truth values in extracted text."""

from anonval.text import find, normal


def test_case_spaces_and_dashes_are_ignored() -> None:
    text = "Paziente:  GIULIA\nBianchi – nata il 12/04/1978"
    assert find(text, "Giulia Bianchi") == [(11, 25)]
    assert find(text, "nato il") == []
    assert find(text, "– nata") == [(26, 32)]


def test_whole_words_only() -> None:
    assert find("Rossini e Rossi", "Rossi") == [(10, 15)]


def test_spaced_characters_match_in_compact_view() -> None:
    text = "C.F. | B | N | C | G | L | I | 7 | 8 |"
    spans = find(text, "BNCGLI78")
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start] == "B" and text[end - 1] == "8"


def test_short_values_do_not_use_compact_view() -> None:
    assert find("A B C", "ABC") == []


def test_markdown_escapes_are_dropped() -> None:
    assert normal("AA\\_071223").text == "aa_071223"


def test_both_plain_and_spaced_forms_are_found() -> None:
    text = "C.F. BNCGLI78D52 e nel modulo B N C G L I 7 8 D 5 2"
    spans = find(text, "BNCGLI78D52")
    assert len(spans) == 2
    assert text[spans[1][0]] == "B" and text[spans[1][1] - 1] == "2"
