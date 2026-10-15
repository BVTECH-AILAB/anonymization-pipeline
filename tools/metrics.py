"""Detection metrics for the anonymization evaluation, computed with standard libraries.

* Precision, recall, F1 and accuracy: scikit-learn, on binary labels.
  - box level (label-agnostic): every required ground-truth box is a positive
    (predicted positive when redaction boxes cover enough of it); every
    redaction box that touches no ground truth is a false positive; boxes that
    only overlap quasi-identifiers (clinical dates, facility addresses) are ignored;
  - word level: every word of the page is PII or not and redacted or not; the
    only level with true negatives, hence the only meaningful accuracy.
* AP and mAP: torchmetrics ``MeanAveragePrecision`` (COCO protocol, pycocotools),
  IoU 0.5, each page an image, quasi-identifiers passed as ``iscrowd`` ignore
  regions. Label-agnostic AP (PII vs not) and mAP over the PII labels.
  Duplicate detections of the same value (a scan's hidden OCR layer and our
  OCR both find it, and both must be redacted) are collapsed first with
  non-maximum suppression (torchvision), as usual before a COCO evaluation.

Only the matching between boxes and labels is ours: it is specific to redaction.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypedDict

import pymupdf
import torch
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from torchmetrics.detection import MeanAveragePrecision
from torchvision.ops import nms


def coverage(target: pymupdf.Rect, rects: Iterable[pymupdf.Rect]) -> float:
    """Share of ``target`` covered by the union of ``rects`` (sampled on a 24x24 grid)."""
    rects = list(rects)
    if target.is_empty:
        return 1.0
    n, hit = 24, 0
    for i in range(n):
        for j in range(n):
            p = pymupdf.Point(
                target.x0 + (i + 0.5) * target.width / n,
                target.y0 + (j + 0.5) * target.height / n,
            )
            hit += any(r.contains(p) for r in rects)
    return hit / (n * n)


def overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    """Intersection area divided by the area of ``a``."""
    area = a.get_area()
    return (a & b).get_area() / area if area else 0.0


def binary_scores(y_true: list[int], y_pred: list[int]) -> dict[str, float | int]:
    """Precision, recall, F1 (positive class = PII) and accuracy with scikit-learn."""
    if not y_true:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "accuracy": 0.0,
            "support": 0,
        }
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", pos_label=1, zero_division=0
    )
    return {
        "precision": round(float(p), 4),
        "recall": round(float(r), 4),
        "f1": round(float(f), 4),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "support": int(sum(y_true)),
    }


def word_labels(
    words: list[pymupdf.Rect],
    pii: list[pymupdf.Rect],
    ignore: list[pymupdf.Rect],
    redacted: list[pymupdf.Rect],
) -> tuple[list[int], list[int]]:
    """Binary labels of the words of one page.

    A word is PII when at least half of it lies in a required ground-truth box,
    skipped when it lies in a quasi-identifier box, redacted when at least half
    of it is covered by redaction boxes.

    Returns:
        ``(y_true, y_pred)``.
    """
    y_true, y_pred = [], []
    for w in words:
        is_pii = any(overlap(w, g) >= 0.5 for g in pii)
        if not is_pii and any(overlap(w, g) >= 0.5 for g in ignore):
            continue
        y_true.append(int(is_pii))
        y_pred.append(int(coverage(w, [r for r in redacted if r.intersects(w)]) >= 0.5))
    return y_true, y_pred


class MapResult(TypedDict):
    ap_pii: float
    map: float
    ap_per_label: dict[str, float]


@dataclass
class DetectionMAP:
    """COCO mAP at IoU 0.5 over the pages of a dataset (torchmetrics).

    Args:
        labels: Label names; their index is the class id.
        nms_iou: IoU above which overlapping detections are duplicates (None: no NMS).
    """

    labels: list[str]
    nms_iou: float | None = 0.5
    _per_class: MeanAveragePrecision = field(init=False)
    _agnostic: MeanAveragePrecision = field(init=False)

    def __post_init__(self) -> None:
        def metric(class_metrics: bool) -> MeanAveragePrecision:
            return MeanAveragePrecision(
                box_format="xyxy",
                iou_type="bbox",
                iou_thresholds=[0.5],
                max_detection_thresholds=[
                    10,
                    100,
                    1000,
                ],  # a page can hold more than 100 boxes
                class_metrics=class_metrics,
            )

        self._per_class = metric(True)
        self._agnostic = metric(False)

    def add_page(
        self,
        preds: list[tuple[pymupdf.Rect, str, float]],
        truths: list[tuple[pymupdf.Rect, str]],
        ignore: list[tuple[pymupdf.Rect, str]],
    ) -> None:
        """One page: predictions ``(rect, label, score)``, required truths and ignore regions ``(rect, label)``."""

        def boxes(rects: list[pymupdf.Rect]) -> torch.Tensor:
            return torch.tensor(
                [[r.x0, r.y0, r.x1, r.y1] for r in rects], dtype=torch.float32
            ).reshape(-1, 4)

        ids = {name: i for i, name in enumerate(self.labels)}
        if preds and self.nms_iou is not None:
            keep = nms(
                boxes([p[0] for p in preds]),
                torch.tensor([p[2] for p in preds], dtype=torch.float32),
                self.nms_iou,
            )
            preds = [preds[i] for i in keep.tolist()]
        gts = truths + ignore
        crowd = torch.tensor([0] * len(truths) + [1] * len(ignore), dtype=torch.int64)
        pred_boxes = boxes([p[0] for p in preds])
        scores = torch.tensor([p[2] for p in preds], dtype=torch.float32)
        gt_boxes = boxes([g[0] for g in gts])
        self._per_class.update(
            [
                {
                    "boxes": pred_boxes,
                    "scores": scores,
                    "labels": torch.tensor(
                        [ids[p[1]] for p in preds], dtype=torch.int64
                    ),
                }
            ],
            [
                {
                    "boxes": gt_boxes,
                    "labels": torch.tensor([ids[g[1]] for g in gts], dtype=torch.int64),
                    "iscrowd": crowd,
                }
            ],
        )
        self._agnostic.update(
            [
                {
                    "boxes": pred_boxes,
                    "scores": scores,
                    "labels": torch.zeros(len(preds), dtype=torch.int64),
                }
            ],
            [
                {
                    "boxes": gt_boxes,
                    "labels": torch.zeros(len(gts), dtype=torch.int64),
                    "iscrowd": crowd,
                }
            ],
        )

    def compute(self) -> MapResult:
        """``ap_pii`` (label-agnostic AP@0.5), ``map`` (mean over labels) and ``ap_per_label``."""
        agnostic = self._agnostic.compute()
        per_class = self._per_class.compute()
        classes = per_class["classes"].reshape(-1).tolist()
        values = per_class["map_per_class"].reshape(-1).tolist()
        per_label = {
            self.labels[c]: round(v, 4)
            for c, v in zip(classes, values, strict=True)
            if v >= 0
        }
        return {
            "ap_pii": round(float(agnostic["map_50"]), 4),
            "map": round(sum(per_label.values()) / len(per_label), 4)
            if per_label
            else 0.0,
            "ap_per_label": per_label,
        }
