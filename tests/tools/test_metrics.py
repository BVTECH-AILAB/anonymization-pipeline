import pymupdf
import pytest
from tools.metrics import DetectionMAP, binary_scores, coverage, overlap, word_labels


def R(x0: float, y0: float, x1: float, y1: float) -> pymupdf.Rect:
    return pymupdf.Rect(x0, y0, x1, y1)


def test_binary_scores_with_true_negatives() -> None:
    y_true = [1] * 10 + [0] * 90
    y_pred = [1] * 8 + [0] * 2 + [1] * 2 + [0] * 88

    scores = binary_scores(y_true, y_pred)

    assert scores["precision"] == pytest.approx(0.8)
    assert scores["recall"] == pytest.approx(0.8)
    assert scores["f1"] == pytest.approx(0.8)
    assert scores["accuracy"] == pytest.approx(0.96)
    assert scores["support"] == 10
    assert binary_scores([], [])["f1"] == 0.0


def test_coverage_and_overlap() -> None:
    assert coverage(R(0, 0, 10, 10), [R(0, 0, 5, 10)]) == pytest.approx(0.5)
    assert coverage(R(0, 0, 0, 0), []) == 1.0
    assert overlap(R(0, 0, 10, 10), R(5, 0, 20, 10)) == pytest.approx(0.5)


def test_word_labels_skip_quasi_identifiers() -> None:
    words = [R(0, 0, 10, 10), R(20, 0, 30, 10), R(40, 0, 50, 10), R(60, 0, 70, 10)]
    pii = [R(0, 0, 10, 10), R(20, 0, 30, 10)]
    ignore = [R(60, 0, 70, 10)]
    redacted = [R(0, 0, 10, 10), R(40, 0, 50, 10), R(60, 0, 70, 10)]

    assert word_labels(words, pii, ignore, redacted) == ([1, 1, 0], [1, 0, 1])


def test_map_ignores_quasi_identifiers_and_penalises_duplicates() -> None:
    metric = DetectionMAP(["PERSON", "PHONE", "DATE"])
    metric.add_page(
        preds=[
            (R(0, 0, 10, 10), "PERSON", 0.9),
            (R(0, 20, 10, 30), "PHONE", 0.5),
            (R(50, 50, 60, 60), "DATE", 0.7),  # on an ignore region: neither TP nor FP
        ],
        truths=[(R(0, 0, 10, 10), "PERSON"), (R(0, 20, 10, 30), "PHONE")],
        ignore=[(R(50, 50, 60, 60), "DATE")],
    )
    metric.add_page(
        preds=[(R(0, 0, 10, 10), "PERSON", 0.8)], truths=[], ignore=[]
    )  # false positive

    result = metric.compute()

    assert result["ap_per_label"]["PHONE"] == pytest.approx(1.0)
    assert result["ap_per_label"]["PERSON"] == pytest.approx(1.0)
    assert "DATE" not in result["ap_per_label"]
    # TP 0.9, FP 0.8, TP 0.5: COCO 101-point AP = (51 * 1 + 50 * 2/3) / 101
    assert result["ap_pii"] == pytest.approx(0.835, abs=0.002)


def test_map_wrong_label_is_a_miss_per_label_but_not_for_pii() -> None:
    metric = DetectionMAP(["ADDRESS", "LOCATION"])
    metric.add_page(
        preds=[(R(0, 0, 10, 10), "LOCATION", 0.9)],
        truths=[(R(0, 0, 10, 10), "ADDRESS")],
        ignore=[],
    )

    result = metric.compute()

    assert result["ap_pii"] == pytest.approx(1.0)
    assert result["ap_per_label"] == {"ADDRESS": 0.0}
    assert result["map"] == 0.0
