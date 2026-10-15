"""OCR text layer: words read by OCR, with one box per character interpolated in the word box.

The interpolation follows the writing direction: left to right for normal text, bottom to top
or top to bottom for the vertical text of the margins.
"""

from anonymization_pipeline.layers import TextLayer, Token
from anonymization_pipeline.ocr import Word
from anonymization_pipeline.pdf.geometry import Rect


def _chars(word: Word) -> list[Rect]:
    x0, y0, x1, y1 = word.rect
    n = max(len(word.text), 1)
    if word.direction == "ltr":
        step = (x1 - x0) / n
        return [
            (x0 + i * step, y0, x0 + (i + 1) * step, y1) for i in range(len(word.text))
        ]
    step = (y1 - y0) / n
    if word.direction == "btt":
        return [
            (x0, y1 - (i + 1) * step, x1, y1 - i * step) for i in range(len(word.text))
        ]
    return [(x0, y0 + i * step, x1, y0 + (i + 1) * step) for i in range(len(word.text))]


def ocr_layer(page_no: int, words: list[Word]) -> TextLayer:
    """Tokens of the OCR words, in reading order, one line per OCR line."""
    tokens = [
        Token(w.text, w.rect, _chars(w), w.line, "ocr", w.conf)
        for w in words
        if w.text.strip()
    ]
    return TextLayer.build(page_no, "ocr", tokens)
