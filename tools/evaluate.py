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

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymupdf

from anonmyzation_pipeline.config import get_settings
from anonmyzation_pipeline.main import build_pipeline
from anonmyzation_pipeline.services.anonymization.redact import redact
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS
from anonmyzation_pipeline.services.anonymization.verify import verify
from tools.dataset.scan import _transform_rect
from tools.metrics import DetectionMAP, binary_scores, coverage, word_labels

_HON = re.compile(r"^(?:Sig\.(?:ra|na)?|Dott\.(?:ssa)?|Prof\.(?:ssa)?|Dr\.)\s+")


def _strip_honorific(r: pymupdf.Rect, text: str) -> pymupdf.Rect:
    """Ground-truth boxes include titles ("Sig.ra Rossi"); only the name must be covered."""
    m = _HON.match(text or "")
    if not m or r.width < r.height:  # horizontal text only
        return r
    return pymupdf.Rect(r.x0 + r.width * len(m.group()) / len(text), r.y0, r.x1, r.y1)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _words(
    dataset: Path, gt: dict, variant: str
) -> dict[int, list[pymupdf.Rect]] | None:
    """Word boxes of every page, in the coordinates of the evaluated file.

    Scans have no text layer of their own: the words of the native twin are
    mapped through the transform the generator applied. Older datasets without
    transforms return None (no word-level metrics).
    """
    native = dataset / "native" / f"{gt['doc_id']}.pdf"
    scan = gt.get("scan")
    if variant != "native" and not (scan and scan.get("transforms")):
        return None
    out: dict[int, list[pymupdf.Rect]] = {}
    with pymupdf.open(native) as doc:
        for pno in range(doc.page_count):
            rects = [pymupdf.Rect(w[:4]) for w in doc[pno].get_text("words")]
            if scan:
                m, s = np.array(scan["transforms"][pno]), scan["dpi"] / 72
                rects = [pymupdf.Rect(_transform_rect(tuple(r), m, s)) for r in rects]
            out[pno] = rects
    return out


def evaluate(
    dataset: Path,
    variant: str,
    out_dir: Path,
    limit: int | None = None,
    cover: float = 0.6,
    nms_iou: float | None = 0.5,
) -> dict:
    """Run the pipeline on every document of a variant and score it."""
    pipe = build_pipeline(get_settings())
    gts = sorted((dataset / "ground_truth" / variant).glob("*.json"))[:limit]
    tot: dict[str, int] = defaultdict(int)
    hit: dict[str, int] = defaultdict(int)
    misses: list[dict] = []
    box_true: list[int] = []
    box_pred: list[int] = []
    word_true: list[int] = []
    word_pred: list[int] = []
    word_metrics = True
    detection = DetectionMAP(list(LABELS), nms_iou=nms_iou)
    fp_boxes = keep_lost = keep_tot = hidden_tot = hidden_leak = 0
    pages = verified = 0
    seconds = 0.0
    work = out_dir / f"anonymized_{variant}"
    work.mkdir(parents=True, exist_ok=True)
    for gtf in gts:
        gt = json.loads(gtf.read_text())
        src = dataset / variant / f"{gt['doc_id']}.pdf"
        dst = work / f"{gt['doc_id']}.pdf"
        t0 = time.perf_counter()
        a = pipe.analyze(src)
        redact(src, a, dst)
        rep = verify(src, dst, a)
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
                continue
            for bx in m["boxes"]:
                text = bx.get("text", m["text"])
                target = _strip_honorific(pymupdf.Rect(bx["rect"]), text)
                tot[m["label"]] += 1
                truths[bx["page"]].append((target, m["label"]))
                c = coverage(target, red[bx["page"]])
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

        words = _words(dataset, gt, variant)
        if words is None:
            word_metrics = False
        else:
            for pno, page_words in words.items():
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
        for m in gt["mentions"]:
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
        for k in gt["keep"]:
            keep_tot += 1
            keep_lost += coverage(pymupdf.Rect(k["rect"]), red[k["page"]]) >= 0.5

    total = sum(tot.values())
    ap = detection.compute()
    report = {
        "variant": variant,
        "documents": len(gts),
        "pages": pages,
        "recall_overall": round(sum(hit.values()) / max(total, 1), 4),
        "box": binary_scores(box_true, box_pred),
        "word": binary_scores(word_true, word_pred) if word_metrics else None,
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
        "hidden_pii_leaked": f"{hidden_leak}/{hidden_tot}",
        "boxes_without_ground_truth": fp_boxes,
        "eponyms_wrongly_redacted": f"{keep_lost}/{keep_tot}",
        "verified_documents": f"{verified}/{len(gts)}",
        "seconds_per_page": round(seconds / max(pages, 1), 2),
        "misses": misses,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"eval_{variant}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False)
    )
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
        "Definitions: box level = every required ground-truth box (covered >= 60% by redaction is a hit) plus every",
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
