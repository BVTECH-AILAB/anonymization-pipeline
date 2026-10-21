import json
from pathlib import Path

import pypdfium2 as pdfium
from tools.dataset.orientation import DOC_ID, MENTIONS, build


def test_every_variant_and_every_mention_is_written(tmp_path: Path) -> None:
    files = build(tmp_path)

    assert [f.parent.name for f in files] == ["native", "scan", "scan_ocr"]
    for f in files:
        assert len(pdfium.PdfDocument(f)) == 3
    gt = json.loads(
        (tmp_path / "ground_truth" / "native" / f"{DOC_ID}.json").read_text()
    )
    assert len(gt["mentions"]) == len(MENTIONS)
    assert all(m["boxes"] for m in gt["mentions"])
    # the label pasted upside down sits in the bottom-right frame of page 1
    record = next(m for m in gt["mentions"] if m["label"] == "MEDICAL_RECORD")
    (box,) = record["boxes"]
    assert box["page"] == 0 and box["rect"][0] > 330 and box["rect"][1] > 640
    scan = json.loads(
        (tmp_path / "ground_truth" / "scan" / f"{DOC_ID}.json").read_text()
    )
    assert len(scan["mentions"]) == len(MENTIONS)
