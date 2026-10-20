"""NER and visual detectors with fake models: no weights, no network."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.detect.ner import (
    CombinedNER,
    Gliner2NER,
    GlinerNER,
)
from anonmyzation_pipeline.services.anonymization.device import resolve_device
from anonmyzation_pipeline.services.anonymization.layers import Word
from tests.documents import letter_pdf, native_words, page_view, scanned_pdf


class FakeGliner2:
    """GLiNER2 stand-in: the shape of ``extract_entities`` with spans and confidence."""

    def extract_entities(self, text: str, labels: list[str], **kwargs) -> dict:
        start = text.index("Verdani")
        return {
            "entities": {
                "person": [
                    {
                        "text": "Verdani",
                        "start": start,
                        "end": start + 7,
                        "confidence": 0.9,
                    }
                ],
                "card_cvv": [{"text": "123", "start": 0, "end": 3, "confidence": 0.9}],
            }
        }


def test_gliner2_spans_are_mapped_and_unknown_labels_ignored() -> None:
    ner = Gliner2NER(model=FakeGliner2())
    text = "123 visita del Sig. Verdani"

    spans = ner.detect(text)

    assert [(s.label, text[s.start : s.end], s.source) for s in spans] == [
        ("PERSON", "Verdani", "gliner2")
    ]


def test_combined_ner_joins_the_spans_and_is_ready_only_when_all_are() -> None:
    first = Gliner2NER(model=FakeGliner2())
    second = Gliner2NER()  # not loaded
    combined = CombinedNER(first, second)

    assert not combined.ready
    second._model = FakeGliner2()
    assert combined.ready
    assert len(combined.detect("Sig. Verdani")) == 2


def test_resolve_device() -> None:
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("cpu", "cuda")


class FakeGliner:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict]:
        self.chunks.append(text)
        i = text.find("Mario Rossi")
        return (
            [
                {
                    "start": i,
                    "end": i + 11,
                    "label": "person",
                    "score": 0.97,
                    "text": "Mario Rossi",
                }
            ]
            if i >= 0
            else []
        )


def test_gliner_offsets_survive_chunking() -> None:
    model = FakeGliner()
    ner = GlinerNER(max_words=5, model=model)
    text = "riga uno con cinque parole\nriga due\nPaziente Mario Rossi oggi\n"

    spans = ner.detect(text)

    assert ner.ready
    assert len(model.chunks) >= 2
    assert [(s.label, text[s.start : s.end]) for s in spans] == [
        ("PERSON", "Mario Rossi")
    ]


def test_visual_codes_and_signatures_on_native_pages(tmp_path: Path) -> None:
    src = letter_pdf(
        tmp_path / "in.pdf", qr_text="https://x.example.it/r?id=1", signature=True
    )
    view = page_view(src)
    found = visual.embedded_images(view)
    codes = visual.codes(view)

    assert sorted(f.label for f in found) == ["QRCODE", "SIGNATURE"]
    assert [c.label for c in codes] == ["QRCODE"]


def test_stamp_needs_a_frame() -> None:
    img = np.full((80, 200, 3), 255, np.uint8)
    img[2:5, :] = img[-5:-2, :] = (40, 50, 160)
    img[:, 2:5] = img[:, -5:-2] = (40, 50, 160)
    img[35:45, 30:170] = (40, 50, 160)

    assert visual._ink_class(img)[0] == "STAMP"
    img[:, :8] = img[:, -8:] = 255
    img[:8, :] = 255
    assert visual._ink_class(img)[0] == "SIGNATURE"


def test_unexplained_ink_finds_a_signature_on_a_scan(tmp_path: Path) -> None:
    native = letter_pdf(tmp_path / "native.pdf")
    scan = scanned_pdf(native, tmp_path / "scan.pdf", scribble=True)
    words = native_words(native)[0]

    found = visual.unexplained_ink(page_view(scan), words)

    assert any(
        f.rect[0] < 330 and f.rect[2] > 400 and 690 < f.rect[3] < 760 for f in found
    )


def test_stamp_from_text() -> None:
    words = [
        Word("Dott.", (400, 100, 425, 110)),
        Word("Bianchi", (428, 100, 470, 110)),
        Word("Medico", (400, 112, 435, 122)),
        Word("Chirurgo", (438, 112, 480, 122)),
        Word("OMCeO", (400, 124, 435, 134)),
        Word("lontano", (100, 600, 140, 610)),
    ]

    stamps = visual.stamp_from_text(0, words)

    assert len(stamps) == 1
    assert stamps[0].rect[0] < 400 and stamps[0].rect[3] > 134


@pytest.mark.parametrize("module", ["google.protobuf", "sentencepiece"])
def test_libraries_the_ner_tokenizers_need_are_installed(module: str) -> None:
    # GLiNER's mdeberta tokenizer reads them without declaring them: a dependency
    # cleanup once removed protobuf and the model stopped loading
    assert importlib.util.find_spec(module) is not None
