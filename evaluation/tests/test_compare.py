import json
from pathlib import Path

from anonymization_eval.evaluate import compare


def _report(folder: Path, recall: float, found: int, misses: int) -> None:
    folder.mkdir(parents=True)
    report = {
        "variant": "native",
        "documents": 1,
        "pages": 2,
        "box": {"recall": recall, "precision": 0.9, "f1": 0.9},
        "word": {"precision": 0.9, "recall": 0.9, "f1": 0.9},
        "ap_pii_50": 0.7,
        "map_50": 0.6,
        "boxes_without_ground_truth": 3,
        "hidden_pii_leaked": "0/2",
        "eponyms_wrongly_redacted": "1/4",
        "verified_documents": "1/1",
        "seconds_per_page": 1.5,
        "recall_per_label": {
            "PERSON": {"found": found, "total": 10, "recall": found / 10, "ap_50": 0.5}
        },
    }
    (folder / "eval_native.json").write_text(json.dumps(report))
    docs = folder / "docs_native"
    docs.mkdir()
    (docs / "000_lettera.json").write_text(
        json.dumps({"stats": {"misses": misses, "fp": 1}})
    )


def test_compare_reports_deltas_per_metric_type_and_document(tmp_path: Path) -> None:
    _report(tmp_path / "baseline", 0.9, 9, 1)
    _report(tmp_path / "candidate", 0.8, 8, 3)

    md = compare(tmp_path / "baseline", tmp_path / "candidate")

    assert "# Evaluation: baseline vs candidate" in md
    assert "| Recall (box) | 0.900 | 0.800 | -0.100 |" in md
    assert "| Boxes with no PII | 3 | 3 | +0 |" in md
    assert "| Hidden PII leaked | 0/2 | 0/2 |  |" in md
    assert "| PERSON | 0.900 (9/10) | 0.800 (8/10) | -0.100 | 0.500 | 0.500 |" in md
    assert "| 000_lettera | +2 | +0 |" in md
    # a variant missing from either folder is skipped
    assert "## scan" not in md
