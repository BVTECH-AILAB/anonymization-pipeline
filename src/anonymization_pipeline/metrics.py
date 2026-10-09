"""Time and performance metrics of one document (``<stem>.metriche.json``).

Only times, counts and resources, never values. The collector lives in a ContextVar: extraction,
detection and the VLM client record without changing signatures. asyncio tasks
(``vlm.gather_or_cancel``) and ``asyncio.to_thread`` copy the context, so they see the collector
of the document; the page ContextVar, set inside the coroutine of a page, stays local to its task
and assigns the VLM calls to the page. Without an active collector every record is a no-op.
"""

import contextlib
import os
import resource
import statistics
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from anonymization_pipeline import __version__
from anonymization_pipeline.domain import Document
from anonymization_pipeline.pipeline_config import PHASES, PipelineConfig


@dataclass
class Call:
    """One VLM call."""

    phase: str
    page: int | None
    duration_s: float
    queue_wait_s: float
    retries: int
    tokens_in: int = 0
    tokens_out: int = 0
    truncated: bool = False
    error: bool = False


@dataclass
class Metrics:
    """Collector of one document: named intervals and VLM calls."""

    intervals: list[tuple[str, int | None, float]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    cpu0: float = field(default_factory=time.process_time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_interval(self, name: str, page: int | None, duration: float) -> None:
        with self._lock:  # also from threads (detectors, docling)
            self.intervals.append((name, page, duration))


_CURRENT: ContextVar[Metrics | None] = ContextVar("metrics", default=None)
_PAGE: ContextVar[int | None] = ContextVar("metrics_page", default=None)


def start(enabled: bool = True) -> Metrics | None:
    """Start the collector of one document in the current context.

    With ``enabled`` False the context gets no collector, so nothing left by a previous document
    of the same task is reused.
    """
    collector = Metrics() if enabled else None
    _CURRENT.set(collector)
    return collector


def current() -> Metrics | None:
    return _CURRENT.get()


def current_page() -> int | None:
    return _PAGE.get()


@contextlib.contextmanager
def measure(name: str, page: int | None = None) -> Iterator[None]:
    """Duration of the block, recorded as interval ``name`` (of the page, if given)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        collector = current()
        if collector is not None:
            collector.add_interval(name, page, time.perf_counter() - t0)


@contextlib.contextmanager
def on_page(index: int) -> Iterator[None]:
    """The VLM calls made in the block are assigned to page ``index`` (zero-based)."""
    token = _PAGE.set(index)
    try:
        yield
    finally:
        _PAGE.reset(token)


def record_call(call: Call) -> None:
    collector = current()
    if collector is not None:
        collector.calls.append(call)


def _r(x: float) -> float:
    return round(x, 3)


def distribution(values: list[float]) -> dict[str, float] | None:
    """Mean, p50, p95 and max (p95 interpolated; with a single value all equal)."""
    if not values:
        return None
    v = sorted(values)
    p95 = statistics.quantiles(v, n=20, method="inclusive")[18] if len(v) > 1 else v[0]
    return {
        "mean": _r(statistics.fmean(v)),
        "p50": _r(statistics.median(v)),
        "p95": _r(p95),
        "max": _r(v[-1]),
    }


def _peak_rss_mb() -> float:
    return round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
    )  # Linux: KiB


def summarize(
    collector: Metrics,
    doc: Document,
    cfg: PipelineConfig,
    times: dict[str, float],
    report: dict[str, Any],
    size: int,
) -> dict[str, Any]:
    """Content of ``<stem>.metriche.json``.

    Args:
        collector: The document collector.
        doc: The processed document.
        cfg: Configuration of the run.
        times: Phase times of the pipeline (the same as in the report).
        report: The document report.
        size: Size of the input in bytes.
    """
    by_name: dict[str, list[tuple[int | None, float]]] = defaultdict(list)
    for name, page, duration in collector.intervals:
        by_name[name].append((page, duration))

    def total(name: str) -> float:
        return _r(sum(d for _, d in by_name.get(name, [])))

    def per_page(name: str) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for page, duration in by_name.get(name, []):
            if page is not None:
                out[page] += duration
        return out

    pages_n = len(doc.pages)
    total_s = times.get("total", 0.0)
    det = cfg.phases.detection.detectors
    active = (
        [k for k in ("rules", "labels") if getattr(det, k)]
        + (["ner"] if det.ner.enabled else [])
        + (["vlm"] if det.vlm else [])
    )
    sources = report.get("detection", {}).get("sources", {})

    def vlm_per_page(phase: str) -> dict[int, float]:
        """Model time per page: sum of the call durations, without the queue wait."""
        out: dict[int, float] = defaultdict(float)
        for call in collector.calls:
            if call.phase == phase and call.page is not None and not call.error:
                out[call.page] += call.duration_s
        return out

    vlm_extraction = vlm_per_page("extraction")
    vlm_detection = vlm_per_page("detection")
    vlm_verification = vlm_per_page("verification")
    extraction = {
        "docling": {
            "pages": sum(1 for p in doc.pages if p.extracted_with == "docling"),
            "duration_s": total("extraction.docling"),
        },
        "vlm": {
            "pages": sum(1 for p in doc.pages if p.extracted_with == "vlm"),
            "duration_s": _r(sum(vlm_extraction.values())),
            "per_page_s": distribution(list(vlm_extraction.values())),
        },
        "image_encoding_s": total("extraction.image_encoding"),
    }

    detection: dict[str, Any] = {}
    for name in active:
        pp = vlm_detection if name == "vlm" else per_page(f"detection.{name}")
        detection[name] = {
            "duration_s": _r(sum(pp.values())),
            "max_page_s": _r(max(pp.values(), default=0.0)),
            "values": sources.get(name),
        }
    detection["union_s"] = total("detection.union")

    vlm: dict[str, Any] = {}
    for phase in dict.fromkeys(c.phase for c in collector.calls):
        calls = [c for c in collector.calls if c.phase == phase]
        ok = [c for c in calls if not c.error]
        duration = sum(c.duration_s for c in ok)
        tokens_out = sum(c.tokens_out for c in ok)
        vlm[phase] = {
            "calls": len(ok),
            "errors": sum(1 for c in calls if c.error),
            "retries": sum(c.retries for c in calls),
            "truncated": sum(1 for c in ok if c.truncated),
            "tokens_in": sum(c.tokens_in for c in ok),
            "tokens_out": tokens_out,
            "duration_s": _r(duration),
            "latency_s": distribution([c.duration_s for c in ok]),
            "queue_wait_s": distribution([c.queue_wait_s for c in calls]),
            "tokens_out_per_s": round(tokens_out / duration, 1)
            if duration > 0
            else None,
        }

    pages = []
    for p in doc.pages:
        i = p.index
        pages.append(
            {
                "page": i + 1,
                "extracted_with": p.extracted_with or None,
                "extraction_s": _r(vlm_extraction[i]) if i in vlm_extraction else None,
                "detection_s": {
                    k: _r(
                        (
                            vlm_detection if k == "vlm" else per_page(f"detection.{k}")
                        ).get(i, 0.0)
                    )
                    for k in active
                },
                "verification_s": _r(vlm_verification.get(i, 0.0)),
                "vlm_calls": sum(
                    1 for c in collector.calls if c.page == i and not c.error
                ),
            }
        )

    verification = report.get("verification", {})
    models = {phase: cfg.model_for(phase) for phase in PHASES}
    return {
        "version": __version__,
        "document": {
            "sha256": report["document"]["sha256"],
            "kind": doc.kind,
            "pages": pages_n,
            "bytes": size,
        },
        "configuration": {
            "backend": cfg.backend.kind,
            "models": {
                phase: {"name": name, "context": spec.context}
                for phase, (name, spec) in models.items()
            },
            "concurrency": cfg.backend.concurrency,
            "extraction": cfg.phases.extraction.native_pdf.engine,
            "detectors": active,
            "passes": len(cfg.passes("detection")) if det.vlm else 0,
            "verification": cfg.phases.verification.mode,
        },
        "times_s": dict(times),
        "throughput": {
            "s_per_page": round(total_s / pages_n, 2) if pages_n else None,
            "pages_per_minute": round(60 * pages_n / total_s, 2) if total_s else None,
        },
        "extraction": extraction,
        "detection": detection,
        "redaction": {
            "iterations": len(by_name.get("redaction.coreference", [])),
            "coreference_s": total("redaction.coreference"),
            "replacement_s": total("redaction.replacement"),
        },
        "verification": {
            "iterations": verification.get("iterations", 0),
            "duration_s": times.get("verification", 0.0),
        },
        "vlm": vlm,
        "pages": pages,
        "resources": {
            "process_cpu_s": _r(time.process_time() - collector.cpu0),
            "process_peak_rss_mb": _peak_rss_mb(),
            "cpus_available": os.cpu_count(),
        },
    }
