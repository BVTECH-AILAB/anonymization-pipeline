import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from tools.evaluate import _pred_view, _write_doc
from tools.viewer import create_app

GT: dict = {
    "doc_id": "e000_test",
    "doc_type": "test",
    "variant": "native",
    "pages": [{"width": 595, "height": 842}],
    "mentions": [
        {
            "id": 1,
            "text": "Mario Rossi",
            "label": "PERSON",
            "required": True,
            "visible": True,
            "kind": "text",
            "boxes": [{"page": 0, "rect": [100, 100, 160, 112], "text": "Mario Rossi"}],
        },
        {
            "id": 2,
            "text": "Ospedale",
            "label": "ORGANIZATION",
            "required": False,
            "visible": True,
            "kind": "text",
            "boxes": [{"page": 0, "rect": [100, 50, 160, 62], "text": "Ospedale"}],
        },
    ],
    "keep": [{"term": "Parkinson", "page": 0, "rect": [100, 200, 150, 212]}],
}


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    dataset, reports = tmp_path / "dataset", tmp_path / "reports"
    (dataset / "native").mkdir(parents=True)
    (dataset / "ground_truth" / "native").mkdir(parents=True)
    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)
    doc.save(dataset / "native" / "e000_test.pdf")
    rotated = pdfium.PdfDocument.new()
    rotated.new_page(595, 842).set_rotation(90)
    rotated.save(dataset / "native" / "e001_rot.pdf")
    (dataset / "ground_truth" / "native" / "e000_test.json").write_text(json.dumps(GT))
    (dataset / "ground_truth" / "native" / "e001_rot.json").write_text(
        json.dumps({**GT, "doc_id": "e001_rot"})
    )
    return dataset, reports


def _scored(reports: Path, doc_id: str = "e000_test") -> None:
    analysis = SimpleNamespace(
        boxes=[
            SimpleNamespace(
                page=0,
                rect=(99, 99, 161, 113),
                label="PERSON",
                text="Mario Rossi",
                score=0.9,
                sources=["ner"],
                layer="native",
                redact=True,
            ),
            SimpleNamespace(
                page=0,
                rect=(300, 300, 340, 310),
                label="PERSON",
                text="Golgi",
                score=0.6,
                sources=["ner"],
                layer="native",
                redact=True,
            ),
            SimpleNamespace(
                page=0,
                rect=(100, 50, 160, 62),
                label="ORGANIZATION",
                text="Ospedale",
                score=0.8,
                sources=["rules"],
                layer="native",
                redact=False,
            ),
        ]
    )
    gt = {**GT, "doc_id": doc_id}
    gt_view = [
        {
            "page": 0,
            "rect": [100, 100, 160, 112],
            "label": "PERSON",
            "text": "Mario Rossi",
            "kind": "text",
            "status": "hit",
            "covered": 1.0,
        },
        {
            "page": 0,
            "rect": [100, 50, 160, 62],
            "label": "ORGANIZATION",
            "text": "Ospedale",
            "kind": "text",
            "status": "quasi",
            "covered": None,
        },
    ]
    (reports / "docs_native").mkdir(parents=True, exist_ok=True)
    keep = [
        {"term": "Parkinson", "page": 0, "rect": [100, 200, 150, 212], "lost": False}
    ]
    stats = {"seconds": 1.2, "verified": True, "issues": [], "leaked": []}
    _write_doc(
        reports / "docs_native" / f"{doc_id}.json",
        gt,
        gt_view,
        _pred_view(analysis, gt),
        keep,
        stats,
    )


def test_predictions_are_matched_against_ground_truth() -> None:
    analysis = SimpleNamespace(
        boxes=[
            SimpleNamespace(
                page=0,
                rect=(99, 99, 161, 113),
                label="PERSON",
                text="x",
                score=1,
                sources=[],
                layer="native",
                redact=True,
            ),
            SimpleNamespace(
                page=0,
                rect=(101, 51, 150, 60),
                label="ORGANIZATION",
                text="x",
                score=1,
                sources=[],
                layer="native",
                redact=True,
            ),
            SimpleNamespace(
                page=0,
                rect=(400, 400, 420, 410),
                label="PHONE",
                text="x",
                score=1,
                sources=[],
                layer="native",
                redact=True,
            ),
            SimpleNamespace(
                page=0,
                rect=(400, 400, 420, 410),
                label="DATE",
                text="x",
                score=1,
                sources=[],
                layer="native",
                redact=False,
            ),
        ]
    )

    assert [p["match"] for p in _pred_view(analysis, GT)] == [
        "tp",
        "quasi",
        "fp",
        "kept",
    ]


def test_documents_and_running_totals(dirs: tuple[Path, Path]) -> None:
    dataset, reports = dirs
    _scored(reports)
    client = TestClient(create_app(dataset, reports))

    j = client.get("/api/docs", params={"variant": "native"}).json()

    assert [d["doc_id"] for d in j["documents"]] == ["e000_test", "e001_rot"]
    assert j["documents"][1]["stats"] is None
    assert (j["totals"]["done"], j["totals"]["total"]) == (1, 2)
    assert j["totals"]["recall"] == 1.0
    assert j["totals"]["precision"] == 0.5
    assert j["run"] is None


def test_document_before_and_after_evaluation(dirs: tuple[Path, Path]) -> None:
    dataset, reports = dirs
    client = TestClient(create_app(dataset, reports))

    pending = client.get("/api/doc/native/e000_test").json()
    assert pending["stats"] is None and pending["pred"] == []
    assert {g["status"] for g in pending["gt"]} == {"pending", "quasi"}

    _scored(reports)
    done = client.get("/api/doc/native/e000_test").json()
    assert done["stats"]["fp"] == 1 and done["stats"]["hits"] == 1
    assert [p["match"] for p in done["pred"]] == ["tp", "fp", "kept"]


def test_rotated_page_boxes_are_in_display_coordinates(dirs: tuple[Path, Path]) -> None:
    dataset, reports = dirs
    client = TestClient(create_app(dataset, reports))

    d = client.get("/api/doc/native/e001_rot").json()

    assert d["pages"][0]["width"] > d["pages"][0]["height"]
    x0, y0, x1, y1 = d["gt"][0]["rect"]
    assert x1 <= d["pages"][0]["width"] and y1 <= d["pages"][0]["height"]
    assert (x1 - x0) < (y1 - y0)  # a horizontal word is vertical on the rotated page


def test_page_image_and_input_validation(dirs: tuple[Path, Path]) -> None:
    dataset, reports = dirs
    client = TestClient(create_app(dataset, reports))

    r = client.get("/api/page/native/e000_test/0", params={"dpi": 50})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert (
        client.get(
            "/api/page/native/e000_test/0", params={"which": "anonymized"}
        ).status_code
        == 404
    )
    assert client.get("/api/page/native/e000_test/3").status_code == 404
    assert client.get("/api/doc/native/..%2Fsecret").status_code in (404, 422)
    assert client.get("/api/doc/other/e000_test").status_code == 422
    assert client.get("/").status_code == 200


def test_only_one_evaluation_runs_at_a_time(dirs: tuple[Path, Path]) -> None:
    dataset, reports = dirs
    release, seen = threading.Event(), []

    def run(variant: str) -> None:
        seen.append(variant)
        release.wait(5)

    client = TestClient(create_app(dataset, reports, run))
    assert (
        client.post("/api/run", json={"variants": ["native", "scan"]}).status_code
        == 202
    )
    assert client.post("/api/run", json={"variants": ["native"]}).status_code == 409
    release.set()
    assert (
        TestClient(create_app(dataset, reports))
        .post("/api/run", json={"variants": ["native"]})
        .status_code
        == 404
    )
