"""Evaluate the anonymizer on the synthetic dataset against ground truth.

Per variant (native, scan, scan_ocr):

* recall per label: a required ground-truth box is found when redaction boxes
  cover at least ``cover`` of it (honorifics like "Dott." may stay visible);
* box level precision / recall / F1 and word level precision / recall / F1 /
  accuracy (scikit-learn), AP and mAP at IoU 0.5 (torchmetrics, COCO): see
  ``tools/metrics.py`` for the definitions;
* hidden PII (white text, metadata, deleted annotations) surviving in the output;
* eponyms wrongly redacted, verification outcome, seconds per page.
"""

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymupdf

from anonmyzation_pipeline.config import get_settings
from anonmyzation_pipeline.main import build_pipeline
from anonmyzation_pipeline.services.anonymization.release import redact_and_verify
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS
from tools.dataset.scan import _transform_rect
from tools.metrics import DetectionMAP, binary_scores, coverage, word_labels

logger = logging.getLogger(__name__)

_HON = re.compile(r"^(?:Sig\.(?:ra|na)?|Dott\.(?:ssa)?|Prof\.(?:ssa)?|Dr\.)\s+")


def _strip_honorific(r: pymupdf.Rect, text: str) -> pymupdf.Rect:
    """Ground-truth boxes include titles ("Sig.ra Rossi"); only the name must be covered."""
    m = _HON.match(text or "")
    if not m or r.width < r.height:  # horizontal text only
        return r
    return pymupdf.Rect(r.x0 + r.width * len(m.group()) / len(text), r.y0, r.x1, r.y1)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _words(dataset: Path, gt: dict) -> dict[int, list[pymupdf.Rect]]:
    """Word boxes of every page, in the coordinates of the evaluated file.

    Scans have no text layer of their own: the words of the native twin are
    mapped through the transform the generator applied.
    """
    native = dataset / "native" / f"{gt['doc_id']}.pdf"
    scan = gt.get("scan")
    out: dict[int, list[pymupdf.Rect]] = {}
    with pymupdf.open(native) as doc:
        for pno in range(doc.page_count):
            rects = [pymupdf.Rect(w[:4]) for w in doc[pno].get_text("words")]
            if scan:
                rects = [r * doc[pno].rotation_matrix for r in rects]
                m, s = np.array(scan["transforms"][pno]), scan["dpi"] / 72
                rects = [pymupdf.Rect(_transform_rect(tuple(r), m, s)) for r in rects]
            out[pno] = rects
    return out


TEST_SHARE = 0.3


def family(doc_id: str) -> str:
    """The template a document was made from: siblings must stay on the same side of the split.

    ``e011_consegne_turno`` -> ``consegne_turno``, ``003_verbale_ps`` -> ``verbale_ps``,
    ``referto_esterno_media_02`` -> ``referto_esterno_media``; a hand-annotated
    document is its own family.
    """
    for pattern in (r"e\d{3}_(.+)", r"\d{3}_(.+)", r"(?!d3_)(.+)_\d{2}"):
        m = re.fullmatch(pattern, doc_id)
        if m:
            return m.group(1)
    return doc_id


def in_split(doc_id: str, split: str) -> bool:
    """Fixed dev/test split by template family: ``test`` is about 30% of the families.

    The test documents are never inspected while tuning the detectors, and no
    sibling of a test document is in dev, so their scores show whether a change
    generalises.
    """
    h = int(hashlib.sha256(family(doc_id).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (h < TEST_SHARE) == (split == "test")


def _view(m: dict, bx: dict, status: str, covered: float | None = None) -> dict:
    """One ground-truth box as the viewer shows it."""
    return {
        "page": bx["page"],
        "rect": bx["rect"],
        "label": m["label"],
        "text": bx.get("text", m["text"]),
        "kind": m["kind"],
        "status": status,
        "covered": covered,
    }


def _pred_view(a, gt: dict) -> list[dict]:
    """Every pipeline box, matched against the ground truth of its page.

    ``tp``: redacted and touching required PII; ``quasi``: redacted, touching only
    quasi-identifiers; ``fp``: redacted, touching no ground truth; ``kept``: found
    but not redacted by policy.
    """
    required: dict[int, list[pymupdf.Rect]] = defaultdict(list)
    other: dict[int, list[pymupdf.Rect]] = defaultdict(list)
    for m in gt["mentions"]:
        for bx in m["boxes"]:
            (required if m["required"] else other)[bx["page"]].append(
                pymupdf.Rect(bx["rect"])
            )
    out = []
    for b in a.boxes:
        r = pymupdf.Rect(b.rect) + (-1, -1, 1, 1)
        if not b.redact:
            match = "kept"
        elif any(r.intersects(g) for g in required[b.page]):
            match = "tp"
        elif any(r.intersects(g) for g in other[b.page]):
            match = "quasi"
        else:
            match = "fp"
        out.append(
            {
                "page": b.page,
                "rect": [round(v, 2) for v in b.rect],
                "label": b.label,
                "text": b.text,
                "score": round(float(b.score), 3),
                "sources": b.sources,
                "layer": b.layer,
                "match": match,
            }
        )
    return out


def _write_doc(
    path: Path, gt: dict, gt_view: list, preds: list, keep: list, stats: dict
) -> None:
    """Per-document result for the viewer (``tools/viewer.py``), written as soon as it is scored."""
    scored = [g for g in gt_view if g["status"] in ("hit", "miss")]
    hits = sum(g["status"] == "hit" for g in scored)
    fp = sum(p["match"] == "fp" for p in preds)
    doc = {
        "doc_id": gt["doc_id"],
        "doc_type": gt["doc_type"],
        "variant": gt["variant"],
        "pages": gt["pages"],
        "gt": gt_view,
        "pred": preds,
        "keep": keep,
        "stats": {
            "required": len(scored),
            "hits": hits,
            "misses": len(scored) - hits,
            "fp": fp,
            "keep_lost": sum(k["lost"] for k in keep),
            **stats,
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False))
    tmp.replace(path)


def evaluate(
    dataset: Path,
    variant: str,
    out_dir: Path,
    limit: int | None = None,
    cover: float = 0.75,
    split: str = "all",
    nms_iou: float | None = 0.5,
) -> dict:
    """Run the pipeline on every document of a variant and score it."""
    settings = get_settings()
    pipe = build_pipeline(settings)
    gts = [
        f
        for f in sorted((dataset / "ground_truth" / variant).glob("*.json"))
        if split == "all" or in_split(f.stem, split)
    ][:limit]
    tot: dict[str, int] = defaultdict(int)
    hit: dict[str, int] = defaultdict(int)
    misses: list[dict] = []
    box_true: list[int] = []
    box_pred: list[int] = []
    word_true: list[int] = []
    word_pred: list[int] = []
    detection = DetectionMAP(list(LABELS), nms_iou=nms_iou)
    fp_boxes = keep_lost = keep_tot = hidden_tot = hidden_leak = 0
    pages = verified = 0
    seconds = 0.0
    work = out_dir / f"anonymized_{variant}"
    work.mkdir(parents=True, exist_ok=True)
    docs_dir = out_dir / f"docs_{variant}"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for old in docs_dir.glob("*.json"):
        old.unlink()
    for n, gtf in enumerate(gts, 1):
        gt = json.loads(gtf.read_text())
        src = dataset / variant / f"{gt['doc_id']}.pdf"
        dst = work / f"{gt['doc_id']}.pdf"
        t0 = time.perf_counter()
        a = pipe.analyze(src)
        rep = redact_and_verify(
            src,
            a,
            dst,
            style=settings.redaction_style,
            tol=settings.verify_pixel_tolerance,
        ).report
        seconds += time.perf_counter() - t0
        pages += len(a.pages)
        verified += rep.ok
        scanned = variant != "native"

        red: dict[int, list[pymupdf.Rect]] = defaultdict(list)
        preds: dict[int, list[tuple[pymupdf.Rect, str, float]]] = defaultdict(list)
        for b in a.boxes:
            if b.redact:
                red[b.page].append(pymupdf.Rect(b.rect) + (-1, -1, 1, 1))
                preds[b.page].append((pymupdf.Rect(b.rect), b.label, b.score))
        gt_view: list[dict] = []
        all_gt: dict[int, list[pymupdf.Rect]] = defaultdict(list)
        truths: dict[int, list[tuple[pymupdf.Rect, str]]] = defaultdict(list)
        ignore: dict[int, list[tuple[pymupdf.Rect, str]]] = defaultdict(list)
        pii_words: dict[int, list[pymupdf.Rect]] = defaultdict(list)
        hidden_words: dict[int, list[pymupdf.Rect]] = defaultdict(list)
        for m in gt["mentions"]:
            for bx in m["boxes"]:
                r = pymupdf.Rect(bx["rect"])
                all_gt[bx["page"]].append(r)
                if not m["required"]:
                    ignore[bx["page"]].append((r, m["label"]))
                elif m["visible"] or not scanned:
                    pii_words[bx["page"]].append(r)
                else:
                    # white text is not in a scan's pixels
                    hidden_words[bx["page"]].append(r)
                if m["required"] and not m["visible"]:
                    # invisible text: neither a hit nor a false alarm for AP
                    ignore[bx["page"]].append((r, m["label"]))
            if (
                not m["required"]
                or m["kind"] not in ("text", "image")
                or not m["visible"]
            ):
                status = "quasi" if not m["required"] else "hidden"
                gt_view += [_view(m, bx, status) for bx in m["boxes"]]
                continue
            for bx in m["boxes"]:
                text = bx.get("text", m["text"])
                target = _strip_honorific(pymupdf.Rect(bx["rect"]), text)
                tot[m["label"]] += 1
                truths[bx["page"]].append((target, m["label"]))
                c = coverage(target, red[bx["page"]])
                gt_view.append(
                    _view(m, bx, "hit" if c >= cover else "miss", round(c, 2))
                )
                box_true.append(1)
                box_pred.append(int(c >= cover))
                if c >= cover:
                    hit[m["label"]] += 1
                else:
                    misses.append(
                        {
                            "doc": gt["doc_id"],
                            "label": m["label"],
                            "text": text[:60],
                            "page": bx["page"] + 1,
                            "covered": round(c, 2),
                        }
                    )
        for pno, rs in red.items():
            unmatched = sum(
                1 for r in rs if not any(r.intersects(g) for g in all_gt[pno])
            )
            fp_boxes += unmatched
            box_true += [0] * unmatched
            box_pred += [1] * unmatched
        for pno in range(len(a.pages)):
            detection.add_page(preds[pno], truths[pno], ignore[pno])

        for pno, page_words in _words(dataset, gt).items():
            visible = [
                w
                for w in page_words
                if not any(w.intersects(h) for h in hidden_words[pno])
            ]
            y_true, y_pred = word_labels(
                visible, pii_words[pno], [g for g, _ in ignore[pno]], red[pno]
            )
            word_true += y_true
            word_pred += y_pred

        # hidden PII: invisible text, metadata, annotation history must not survive
        with pymupdf.open(dst) as d:
            out_text = _norm(" ".join(d[i].get_text() for i in range(d.page_count)))
            raw = b"".join(
                (d.xref_stream(i) or b"") for i in range(1, d.xref_length())
            ).lower()
            md = _norm(
                " ".join(str(v) for v in (d.metadata or {}).values() if v)
                + (d.get_xml_metadata() or "")
            )
        for m in (
            gt["mentions"] if variant != "scan" else []
        ):  # a raster has no hidden layer
            if (
                m["required"]
                and (not m["visible"] or m["kind"] in ("metadata", "annotation"))
                and len(m["text"]) >= 5
            ):
                hidden_tot += 1
                v = _norm(m["text"])
                if (
                    v in out_text
                    or v in md
                    or m["text"].lower().encode("latin-1", "ignore") in raw
                ):
                    hidden_leak += 1
                    misses.append(
                        {
                            "doc": gt["doc_id"],
                            "label": m["label"],
                            "text": m["text"][:60],
                            "page": 0,
                            "covered": "hidden",
                        }
                    )
        keep_view = []
        for k in gt["keep"]:
            lost = coverage(pymupdf.Rect(k["rect"]), red[k["page"]]) >= 0.5
            keep_tot += 1
            keep_lost += lost
            keep_view.append({**k, "lost": bool(lost)})
        _write_doc(
            docs_dir / f"{gt['doc_id']}.json",
            gt,
            gt_view,
            _pred_view(a, gt),
            keep_view,
            {
                "seconds": round(time.perf_counter() - t0, 2),
                "verified": rep.ok,
                "issues": rep.issues,
                "leaked": [
                    x["text"]
                    for x in misses
                    if x["doc"] == gt["doc_id"] and x["covered"] == "hidden"
                ],
            },
        )
        tp = sum(t and p for t, p in zip(box_true, box_pred, strict=True))
        logger.info(
            "%s %d/%d %s: recall %.3f, precision %.3f, %.1f s/page",
            variant,
            n,
            len(gts),
            gt["doc_id"],
            tp / max(sum(box_true), 1),
            tp / max(sum(box_pred), 1),
            seconds / max(pages, 1),
        )

    total = sum(tot.values())
    ap = detection.compute()
    report = {
        "variant": variant,
        "split": split,
        "documents": len(gts),
        "pages": pages,
        "recall_overall": round(sum(hit.values()) / max(total, 1), 4),
        "box": binary_scores(box_true, box_pred),
        "word": binary_scores(word_true, word_pred),
        "ap_pii_50": ap["ap_pii"],
        "map_50": ap["map"],
        "recall_per_label": {
            k: {
                "found": hit[k],
                "total": tot[k],
                "recall": round(hit[k] / tot[k], 3),
                "ap_50": ap["ap_per_label"].get(k),
            }
            for k in sorted(tot, key=lambda k: -tot[k])
        },
        "hidden_pii_leaked": f"{hidden_leak}/{hidden_tot}"
        if variant != "scan"
        else "n/a",
        "boxes_without_ground_truth": fp_boxes,
        "eponyms_wrongly_redacted": f"{keep_lost}/{keep_tot}",
        "verified_documents": f"{verified}/{len(gts)}",
        "seconds_per_page": round(seconds / max(pages, 1), 2),
        "misses": misses,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"eval_{variant}.json.tmp"  # the viewer may read it meanwhile
    tmp.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    tmp.replace(out_dir / f"eval_{variant}.json")
    return report


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def to_markdown(reports: list[dict]) -> str:
    """Summary table, per-label table and every miss."""
    head = [
        "| | " + " | ".join(r["variant"] for r in reports) + " |",
        "|---|" + "---|" * len(reports),
    ]
    rows = [
        ("Documents", lambda r: r["documents"]),
        ("Pages", lambda r: r["pages"]),
        ("**Recall** (box, required PII)", lambda r: r["box"]["recall"]),
        ("**Precision** (box)", lambda r: r["box"]["precision"]),
        ("**F1** (box)", lambda r: r["box"]["f1"]),
        ("Precision (word)", lambda r: r["word"] and r["word"]["precision"]),
        ("Recall (word)", lambda r: r["word"] and r["word"]["recall"]),
        ("F1 (word)", lambda r: r["word"] and r["word"]["f1"]),
        ("**Accuracy** (word)", lambda r: r["word"] and r["word"]["accuracy"]),
        ("**AP@0.5** (PII, label-agnostic)", lambda r: r["ap_pii_50"]),
        ("**mAP@0.5** (mean over labels)", lambda r: r["map_50"]),
        ("Hidden PII leaked", lambda r: r["hidden_pii_leaked"]),
        (
            "Boxes with no PII (over-redaction)",
            lambda r: r["boxes_without_ground_truth"],
        ),
        ("Eponyms wrongly redacted", lambda r: r["eponyms_wrongly_redacted"]),
        ("Verified documents", lambda r: r["verified_documents"]),
        ("Seconds per page", lambda r: r["seconds_per_page"]),
    ]
    lines = ["# Anonymization evaluation (synthetic dataset)", "", *head]
    lines += [
        f"| {name} | " + " | ".join(_fmt(get(r)) for r in reports) + " |"
        for name, get in rows
    ]
    labels = sorted({k for r in reports for k in r["recall_per_label"]})
    lines += [
        "",
        "## Recall and AP@0.5 per PII type",
        "",
        "| Type | "
        + " | ".join(f"{r['variant']} recall | {r['variant']} AP@0.5" for r in reports)
        + " |",
        "|---|" + "---|---|" * len(reports),
    ]
    for lab in labels:
        cells = []
        for r in reports:
            x = r["recall_per_label"].get(lab)
            cells += (
                [f"{x['recall']:.0%} ({x['found']}/{x['total']})", _fmt(x["ap_50"])]
                if x
                else ["–", "–"]
            )
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Definitions: box level = every required ground-truth box (covered >= 75% by redaction is a hit) plus every",
        "redaction box touching no ground truth (false positive); word level = every word of the page, PII or not,",
        "redacted or not (quasi-identifiers skipped); AP/mAP = COCO protocol (torchmetrics), IoU 0.5, quasi-identifiers",
        "and invisible text as ignore regions, duplicates collapsed by NMS (torchvision, IoU 0.5).",
        "Precision/recall/F1/accuracy computed with scikit-learn.",
    ]
    for r in reports:
        if r["misses"]:
            lines += [
                "",
                f"## Misses · {r['variant']}",
                "",
                "| Document | Page | Type | Text | Coverage |",
                "|---|---|---|---|---|",
            ]
            lines += [
                f"| {m['doc']} | {m['page']} | {m['label']} | {m['text']} | {m['covered']} |"
                for m in r["misses"]
            ]
    return "\n".join(lines) + "\n"
