"""Evaluation viewer: ground truth on the left, predictions on the right, document by document.

Dev only, like the rest of ``tools/``: it reads the synthetic dataset and the
per-document results that ``tools/evaluate.py`` writes to
``reports/docs_<variant>/<doc_id>.json`` while it runs, so an evaluation
started here or with ``just evaluate`` shows up live. Binds to localhost.
"""

import io
import json
import logging
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import pypdfium2 as pdfium
from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from anonmyzation_pipeline.services.anonymization.geometry import to_display
from anonmyzation_pipeline.services.anonymization.pdf_pages import PageView, open_pages

logger = logging.getLogger(__name__)

Variant = Literal["native", "scan", "scan_ocr"]
DocId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_\-]{1,120}$")]
_PAGE = Path(__file__).with_name("viewer.html")


class RunRequest(BaseModel):
    variants: list[Variant] = Field(min_length=1, max_length=3)


class Runner:
    """One evaluation at a time, in a background thread."""

    def __init__(self, run: Callable[[Variant], None]):
        self._run = run
        self._lock = threading.Lock()
        self.state: dict = {"running": False, "variant": None, "error": None}

    def start(self, variants: list[Variant]) -> bool:
        with self._lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "variant": variants[0], "error": None}
        threading.Thread(target=self._work, args=(variants,), daemon=True).start()
        return True

    def _work(self, variants: list[Variant]) -> None:
        try:
            for v in variants:
                self.state["variant"] = v
                self._run(v)
        except Exception as e:
            logger.error("Evaluation failed", exc_info=e)
            self.state["error"] = type(e).__name__
        finally:
            self.state["running"] = False


def _display(rect: list[float], info: dict) -> list[float]:
    """Text coordinates are unrotated; the rendered page is rotated."""
    x0, y0, x1, y1 = rect
    return [round(v, 2) for v in to_display((x0, y0, x1, y1), info)]


def create_app(
    dataset: Path, reports: Path, run: Callable[[Variant], None] | None = None
) -> FastAPI:
    """Viewer app over ``dataset`` and the evaluation output in ``reports``.

    Args:
        dataset: Dataset folder (``native/``, ``scan/``, ``scan_ocr/``, ``ground_truth/``).
        reports: Evaluation output folder.
        run: Scores one variant; ``None`` disables starting an evaluation from the page.
    """
    app = FastAPI(title="Evaluation viewer", docs_url=None, redoc_url=None)
    runner = Runner(run) if run else None

    def pdf_path(variant: str, doc_id: str, which: str = "original") -> Path:
        base = (
            dataset / variant
            if which == "original"
            else reports / f"anonymized_{variant}"
        )
        path = base / f"{doc_id}.pdf"
        if not path.is_file():
            raise HTTPException(404, "Documento non trovato")
        return path

    def read_json(path: Path) -> dict | None:
        """A result file, or None while it is missing or being replaced by a running evaluation."""
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def record(variant: str, doc_id: str) -> dict | None:
        return read_json(reports / f"docs_{variant}" / f"{doc_id}.json")

    @lru_cache(maxsize=256)
    def render(path: Path, _mtime: float, page: int, dpi: int) -> bytes:
        """PNG of one page; ``_mtime`` keys the cache so a re-evaluated output is re-rendered."""
        pdf = pdfium.PdfDocument(path)
        try:
            if not 0 <= page < len(pdf):
                raise HTTPException(404, "Pagina non trovata")
            img = PageView(pdf[page], page).render(dpi)
        finally:
            pdf.close()
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, format="PNG")
        return buf.getvalue()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(_PAGE.read_text(encoding="utf-8"))

    @app.get("/api/docs")
    def docs(variant: Variant = "native") -> dict:
        """Every document of the variant with its result so far, plus running totals."""
        items, tot = [], {"required": 0, "hits": 0, "fp": 0, "seconds": 0.0, "done": 0}
        for f in sorted((dataset / "ground_truth" / variant).glob("*.json")):
            rec = record(variant, f.stem)
            stats = rec["stats"] if rec else None
            if stats:
                tot["done"] += 1
                for k in ("required", "hits", "fp", "seconds"):
                    tot[k] += stats[k]
            items.append({"doc_id": f.stem, "stats": stats})
        tp, pred = tot["hits"], tot["hits"] + tot["fp"]
        recall = tp / tot["required"] if tot["required"] else None
        precision = tp / pred if pred else None
        f1 = (
            2 * recall * precision / (recall + precision)
            if recall and precision
            else None
        )
        final = reports / f"eval_{variant}.json"
        summary = None
        docs_dir = reports / f"docs_{variant}"
        newest_doc = max(
            (f.stat().st_mtime for f in docs_dir.glob("*.json")), default=0.0
        )
        r = read_json(final)
        # only the report of the run whose documents are shown (not one still running elsewhere)
        current = r is not None and final.stat().st_mtime >= newest_doc
        if current and r is not None and not (runner and runner.state["running"]):
            summary = {
                k: r.get(k)
                for k in (
                    "ap_pii_50",
                    "map_50",
                    "word",
                    "hidden_pii_leaked",
                    "verified_documents",
                )
            }
        return {
            "documents": items,
            "totals": {
                **tot,
                "total": len(items),
                "recall": recall,
                "precision": precision,
                "f1": f1,
            },
            "final": summary,
            "run": runner.state if runner else None,
        }

    @app.get("/api/doc/{variant}/{doc_id}")
    def doc(variant: Variant, doc_id: DocId) -> dict:
        """Ground truth and predictions of one document, in display coordinates."""
        gt_file = dataset / "ground_truth" / variant / f"{doc_id}.json"
        if not gt_file.is_file():
            raise HTTPException(404, "Documento non trovato")
        rec: dict | None = record(variant, doc_id)
        if rec is None:
            gt = json.loads(gt_file.read_text())
            rec = {
                "doc_id": doc_id,
                "doc_type": gt["doc_type"],
                "gt": [
                    {
                        "page": b["page"],
                        "rect": b["rect"],
                        "label": m["label"],
                        "text": b.get("text", m["text"]),
                        "kind": m["kind"],
                        "status": "pending"
                        if m["required"] and m["visible"]
                        else ("quasi" if not m["required"] else "hidden"),
                        "covered": None,
                    }
                    for m in gt["mentions"]
                    for b in m["boxes"]
                ],
                "pred": [],
                "keep": [{**k, "lost": False} for k in gt["keep"]],
                "stats": None,
            }
        pdf = pdfium.PdfDocument(pdf_path(variant, doc_id))
        try:
            infos = [view.info for view in open_pages(pdf)]
        finally:
            pdf.close()
        rec["pages"] = [{"width": i["width"], "height": i["height"]} for i in infos]
        for key in ("gt", "pred", "keep"):
            for item in rec[key]:
                item["rect"] = _display(item["rect"], infos[item["page"]])
        rec["anonymized"] = (
            reports / f"anonymized_{variant}" / f"{doc_id}.pdf"
        ).is_file() and rec["stats"] is not None
        return rec

    @app.get("/api/page/{variant}/{doc_id}/{page}")
    def page_image(
        variant: Variant,
        doc_id: DocId,
        page: Annotated[int, PathParam(ge=0, le=500)],
        which: Literal["original", "anonymized"] = "original",
        dpi: Annotated[int, Query(ge=40, le=200)] = 110,
    ) -> Response:
        path = pdf_path(variant, doc_id, which)
        return Response(
            render(path, path.stat().st_mtime, page, dpi), media_type="image/png"
        )

    @app.post("/api/run", status_code=202)
    def start(req: RunRequest) -> dict:
        if runner is None:
            raise HTTPException(404, "Avvio non disponibile")
        if not runner.start(req.variants):
            raise HTTPException(409, "Valutazione già in corso")
        return runner.state

    return app
