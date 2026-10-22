"""The evaluation judge reads PDFs only under PDFIUM_LOCK, like the engine it measures."""

from pathlib import Path

import pytest

from evals.pdf import document_words, file_content
from tests.documents import letter_pdf, record_unlocked_pdfium


def test_evaluation_judge_uses_pdfium_only_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = letter_pdf(tmp_path / "lettera.pdf")
    unlocked = record_unlocked_pdfium(monkeypatch)

    document_words(letter)
    file_content(letter)

    assert unlocked == []
