"""Compare OCR engines on the scanned dataset against ground truth.

Engines: docTR (the service default, word-level boxes) and LightOnOCR-3
(better reading, block-level boxes only, much slower through transformers).
Measures, per scanned page: share of ground-truth PII strings read exactly
or with fuzzy match >= 90, bag-of-words F1 against the native text,
localization of PII boxes (docTR) and seconds per page.
"""

import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
import torch
from PIL import Image
from rapidfuzz import fuzz
from transformers import AutoModelForImageTextToText, AutoProcessor

from anonmyzation_pipeline.services.anonymization.ocr import DoctrOCR, render_page


class LightOnOCR:
    """LightOnOCR-3 through transformers: page transcription (``prompt=""``)
    or blocks with boxes normalised to 0-1000 (``prompt="grounding"``)."""

    def __init__(
        self, model_id: str = "lightonai/LightOnOCR-3-0.8B", max_side: int = 2048
    ) -> None:
        self.model_id, self.max_side = model_id, max_side
        self._model = None
        self._processor = None

    def _load(self) -> None:
        if self._model is None:
            self._model = AutoModelForImageTextToText.from_pretrained(
                self.model_id, dtype=torch.bfloat16, device_map="cuda"
            )
            self._processor = AutoProcessor.from_pretrained(self.model_id)

    def raw(self, page: pymupdf.Page, prompt: str = "") -> str:
        """Model output for one page."""
        self._load()
        model, proc = self._model, self._processor
        if model is None or proc is None:
            raise RuntimeError("LightOnOCR not loaded")
        dpi = min(400, int(self.max_side * 72 / max(page.rect.width, page.rect.height)))
        content: list[dict] = [
            {"type": "image", "image": Image.fromarray(render_page(page, dpi))}
        ]
        if prompt:
            content.append({"type": "text", "text": prompt})
        inputs = proc.apply_chat_template(
            [{"role": "user", "content": content}],
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=False,
        ).to(model.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
        return proc.decode(
            out[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )

    def page_text(self, page: pymupdf.Page) -> str:
        return self.raw(page, "")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[#*|_`>\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def bow_f1(ref: str, hyp: str) -> float:
    r, h = Counter(norm(ref).split()), Counter(norm(hyp).split())
    tp = sum((r & h).values())
    if not tp:
        return 0.0
    p, rc = tp / sum(h.values()), tp / sum(r.values())
    return 2 * p * rc / (p + rc)


def run(
    dataset: Path, engines: list[str], limit: int | None = None, out: Path | None = None
) -> dict:
    gts = sorted((dataset / "ground_truth" / "scan").glob("*.json"))[:limit]
    impls = {
        "doctr": DoctrOCR(),
        "doctr_fast": DoctrOCR("fast_base", "crnn_vgg16_bn"),
        "lighton": LightOnOCR(),
        "lighton_1b": LightOnOCR("lightonai/LightOnOCR-3-1B"),
    }
    stats = {e: defaultdict(float) for e in engines}
    samples = {}
    for gtf in gts:
        gt = json.loads(gtf.read_text())
        scan = pymupdf.open(dataset / "scan" / f"{gt['doc_id']}.pdf")
        native = pymupdf.open(dataset / "native" / f"{gt['doc_id']}.pdf")
        for pno, page in enumerate(scan):
            ref = native[pno].get_text()
            mentions = [
                (m, b)
                for m in gt["mentions"]
                if m["kind"] == "text" and m["visible"] and m["required"]
                for b in m["boxes"]
                if b["page"] == pno
            ]
            for e in engines:
                eng = impls[e]
                t0 = time.perf_counter()
                if e.startswith("doctr"):
                    words = eng.page_words(page)
                    text = " ".join(w.text for w in words)
                else:
                    words, text = None, eng.page_text(page)
                dt = time.perf_counter() - t0
                if (gt["doc_id"], pno) not in samples and e.startswith("lighton"):
                    samples[(gt["doc_id"], pno)] = text[:1500]
                s = stats[e]
                s["pages"] += 1
                s["seconds"] += dt
                s["bow_f1"] += bow_f1(ref, text)
                nt = norm(text)
                for _m, b in mentions:
                    target = norm(b["text"])
                    if not target:
                        continue
                    s["pii"] += 1
                    s["pii_exact"] += target in nt
                    s["pii_fuzzy"] += fuzz.partial_ratio(target, nt) >= 90
                    if words is not None:
                        r = pymupdf.Rect(b["rect"]) + (-3, -3, 3, 3)
                        inside = [
                            w
                            for w in words
                            if r.contains(
                                pymupdf.Rect(w.rect).tl
                                + (pymupdf.Rect(w.rect).br - pymupdf.Rect(w.rect).tl)
                                * 0.5
                            )
                        ]
                        s["pii_localized"] += (
                            bool(inside)
                            and fuzz.ratio(
                                target, norm(" ".join(w.text for w in inside))
                            )
                            >= 85
                        )
    report = {}
    for e, s in stats.items():
        n, p = max(s["pii"], 1), max(s["pages"], 1)
        report[e] = {
            "pages": int(s["pages"]),
            "sec_per_page": round(s["seconds"] / p, 2),
            "page_text_bow_f1": round(s["bow_f1"] / p, 3),
            "pii_strings": int(s["pii"]),
            "pii_exact": round(s["pii_exact"] / n, 3),
            "pii_fuzzy90": round(s["pii_fuzzy"] / n, 3),
            "pii_localized": round(s["pii_localized"] / n, 3)
            if e.startswith("doctr")
            else None,
        }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "ocr_benchmark.json").write_text(json.dumps(report, indent=1))
        (out / "lighton_samples.txt").write_text(
            "\n\n=====\n".join(f"{k}\n{v}" for k, v in samples.items())
        )
    return report
