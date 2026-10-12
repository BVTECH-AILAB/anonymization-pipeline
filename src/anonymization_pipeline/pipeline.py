"""Orchestration: ingest -> extraction -> detection -> [redaction -> verification]* -> Markdown.

The VLM (one model for every phase) decides what is written in the non-native pages, what is
sensitive, which values are the same entity, what slipped through. CPU components work next to
the VLM: docling extracts the native PDF pages, rules, labels and NER detect in union with the
VLM. The code does I/O, validates the detectors' output and replaces the strings.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from anonymization_pipeline import __version__, metrics
from anonymization_pipeline.detection import detect
from anonymization_pipeline.domain import Document, Group, Occurrence, Residual, Result
from anonymization_pipeline.extraction import extract
from anonymization_pipeline.ingest import load
from anonymization_pipeline.pipeline_config import PHASES, PipelineConfig
from anonymization_pipeline.redaction import (
    group_entities,
    leftovers,
    redact,
    structure,
    vault,
)
from anonymization_pipeline.rendering import attach, attachment, render_pdf, text_of
from anonymization_pipeline.text import tag_category
from anonymization_pipeline.verification import UnredactableContent, verify
from anonymization_pipeline.vlm import VLMClient, VLMError, encode_image

logger = logging.getLogger(__name__)

# progress(phase, data): called at the start of every phase
Progress = Callable[[str, dict[str, Any]], None]


class MissingKeyError(ValueError):
    """Pseudonym mode without a key for the map (Italian message for users)."""


def _join_pages(
    doc: Document,
    pages: list[str],
    occurrences: list[list[Occurrence]],
    cfg: PipelineConfig,
) -> tuple[str, list[Occurrence]]:
    """Markdown of the document and occurrences with offsets in the whole document."""
    parts: list[str] = []
    found: list[Occurrence] = []
    length = 0
    for i, (text, page_occurrences) in enumerate(zip(pages, occurrences, strict=True)):
        if i and doc.visual:
            separator = cfg.output.page_separator.replace("{n}", str(i + 1))
            parts.append(separator)
            length += len(separator)
        found += [
            Occurrence(
                o.start + length, o.end + length, o.replacement, o.original, o.category
            )
            for o in page_occurrences
        ]
        parts.append(text)
        length += len(text)
    return "".join(parts), found


def check_invariant(
    pages: list[str], occurrences: list[list[Occurrence]], cfg: PipelineConfig
) -> None:
    """No replaced value may stay in clear text anywhere else in the document (a check of the code).

    Raises:
        UnredactableContent: A replaced value is still in clear text.
    """
    values = {(o.original, o.category) for found in occurrences for o in found}
    if left := leftovers(pages, values, cfg):
        raise UnredactableContent("invariant", left)


def _layout(
    doc: Document,
    groups: list[Group],
    markdown: str,
    occurrences: list[Occurrence],
    cfg: PipelineConfig,
) -> tuple[bytes, bytes, dict[str, Any]] | None:
    """Censored layout PDF (structure attached), structure JSON and the map section; None (fail
    closed) when the structure or the PDF could hold what the censored Markdown does not."""
    with metrics.measure("layout.structure"):
        clear = structure.build_structure(doc, cfg)
        censored = structure.censor(clear, groups, cfg)
    try:
        structure.check(censored, markdown, occurrences, cfg)
        with metrics.measure("layout.pdf"):
            data = structure.canonical(censored.document)
            pdf = render_pdf(
                censored.document,
                censored.marks,
                highlight=cfg.phases.redaction.mode != "redaction",
            )
            pdf = attach(pdf, structure.ATTACHMENT, data)
        values = {
            (o.original, o.category) for o in [*occurrences, *censored.occurrences]
        }
        if leftovers(text_of(pdf), values, cfg):
            raise structure.StructureLeak("replaced value in the PDF text")
    except structure.StructureLeak as exc:
        logger.warning("Layout PDF not produced: %s", exc)
        return None
    return pdf, data, structure.map_section(clear, censored)


def _occurrence_dicts(occurrences: list[Occurrence]) -> list[dict[str, Any]]:
    return [
        {
            "start": o.start,
            "end": o.end,
            "replacement": o.replacement,
            "original": o.original,
        }
        for o in occurrences
    ]


async def anonymize(
    name: str,
    data: bytes,
    cfg: PipelineConfig,
    client: VLMClient,
    key: str | None = None,
    progress: Progress | None = None,
) -> Result:
    """Anonymize one document.

    Args:
        name: File name (only its extension is used; never logged).
        data: File content.
        cfg: Configuration of the run.
        client: VLM client of the run.
        key: Key or passphrase of the pseudonym map (pseudonym mode).
        progress: Called at the start of every phase; the first time with the metrics collector
            in ``data["metrics"]``, to count the VLM calls live.

    Raises:
        MissingKeyError: Pseudonym mode without a key.
        DocumentError: Invalid document.
        UnredactableContent: Residuals left, verification impossible or invariant broken.
        VLMError: The VLM backend failed outside the verification.
    """
    phases = cfg.phases
    if phases.redaction.mode == "pseudonym" and not key:
        raise MissingKeyError(
            "Modalità pseudonimo: serve una chiave per la mappa, oppure usare la modalità tag"
        )
    times: dict[str, float] = {}
    collector = metrics.start(cfg.output.save_metrics)
    t0 = t = time.perf_counter()

    def advance(phase: str, **details: Any) -> None:
        if progress is not None:
            progress(phase, details)

    def mark(phase: str) -> None:
        nonlocal t
        now = time.perf_counter()
        times[phase] = round(times.get(phase, 0) + now - t, 2)
        t = now

    advance("ingest", metrics=collector)
    doc = await asyncio.to_thread(load, name, data, cfg)
    mark("ingest")

    warnings: list[str] = []
    advance("extraction", pages=len(doc.pages))
    warnings += await extract(doc, client, cfg)
    if phases.detection.use_image and cfg.model_for("detection")[1].vision:
        with metrics.measure("extraction.image_encoding"):
            for page in doc.pages:
                if page.image is not None:
                    page.encoded_image = encode_image(page.image, cfg)
    mark("extraction")

    advance("detection")
    outcome = await detect(doc, client, cfg)
    warnings += outcome.warnings
    mark("detection")

    texts = [p.text for p in doc.pages]
    entities = list(outcome.entities)
    verification: dict[str, Any] = {
        "mode": phases.verification.mode,
        "outcome": "off",
        "residuals": [],
    }
    discarded_by_verification = 0
    advance("redaction")
    groups = group_entities(entities)
    with metrics.measure("redaction.replacement"):
        pages, occurrences = redact(texts, groups, cfg)
    mark("redaction")
    if phases.verification.mode != "off":
        advance("verification")
        try:
            found = await verify(doc, pages, client, cfg)
        except VLMError as exc:
            mark("verification")
            if phases.verification.required:
                raise UnredactableContent("verification_unavailable") from exc
            warnings.append("verification_skipped")
            verification["outcome"] = "skipped"
        else:
            mark("verification")
            discarded_by_verification = found.hallucinations_discarded
            residuals = [Residual(e.category, e.page + 1) for e in found.entities]
            if not residuals:
                verification["outcome"] = "ok"
            elif phases.verification.mode == "block":
                raise UnredactableContent("residuals", residuals)
            else:
                verification["outcome"] = "residuals"
                verification["residuals"] = UnredactableContent(
                    "residuals", residuals
                ).as_dict()["residuals"]
                warnings.append("residuals_not_fixed")

    check_invariant(pages, occurrences, cfg)
    markdown, joined = _join_pages(doc, pages, occurrences, cfg)
    layout = None
    if cfg.output.layout_pdf:
        layout = await asyncio.to_thread(_layout, doc, groups, markdown, joined, cfg)
        if layout is None:
            warnings.append("layout_unavailable")
    doc_sha = vault.sha256(data)
    encrypted_map = None
    if phases.redaction.mode == "pseudonym" and key:
        content = vault.map_content(
            name,
            doc_sha,
            markdown,
            _occurrence_dicts(joined),
            layout[2] if layout else None,
        )
        encrypted_map = vault.encrypt_map(content, key, doc_sha)
    times["total"] = round(time.perf_counter() - t0, 2)
    per_group = (
        Counter(o.replacement for o in joined)
        if phases.redaction.mode != "redaction"
        else Counter()
    )
    report: dict[str, Any] = {
        "version": __version__,
        "document": {"sha256": doc_sha, "kind": doc.kind, "pages": len(doc.pages)},
        "extraction": {
            "docling": [
                p.index + 1 for p in doc.pages if p.extracted_with == "docling"
            ],
            "vlm": [
                {"page": p.index + 1, "reason": p.reason or "docling_unavailable"}
                for p in doc.pages
                if p.extracted_with == "vlm"
            ],
        },
        "model": {
            "backend": cfg.backend.kind,
            "phases": {phase: cfg.model_for(phase)[0] for phase in PHASES},
        },
        "mode": phases.redaction.mode,
        "counts": dict(sorted(Counter(o.category for o in joined).items())),
        "entities": [
            {
                "replacement": g.tag,
                "category": g.category,
                "variants": len(g.values),
                "sources": sorted({e.source for e in g.values}),
                "occurrences": per_group.get(g.tag),
            }
            for g in groups
            if g.tag
        ],
        "detection": {
            "values": len(outcome.entities),
            "sources": outcome.sources,
            "hallucinations_discarded": outcome.hallucinations_discarded
            + discarded_by_verification,
            "inactive_categories": outcome.inactive_categories,
            "eponyms_discarded": outcome.eponyms_discarded,
        },
        "redaction": {"groups": len(groups)},
        "verification": verification,
        "layout": ("pdf" if layout else "unavailable")
        if cfg.output.layout_pdf
        else "off",
        "vlm": client.summary(),
        "times_s": times,
        "warnings": warnings,
    }
    transcript = None
    positions: list[dict[str, Any]] | None = None
    if cfg.output.save_transcript:
        transcript, _ = _join_pages(doc, texts, [[] for _ in texts], cfg)
        # positions of every occurrence in the transcript too (CLEAR TEXT: only with the transcript)
        content = vault.map_content(name, doc_sha, markdown, _occurrence_dicts(joined))
        _, positions = vault.restore_detail(markdown, content)
        for position, o in zip(
            positions, sorted(joined, key=lambda o: o.start), strict=True
        ):
            position["category"] = o.category
    tags = [
        {
            "start": o.start,
            "end": o.end,
            "replacement": o.replacement,
            "category": o.category,
        }
        for o in sorted(joined, key=lambda o: o.start)
    ]
    summary = (
        metrics.summarize(collector, doc, cfg, times, report, len(data))
        if collector
        else None
    )
    advance("completed")
    return Result(
        name=name,
        markdown=markdown,
        report=report,
        encrypted_map=encrypted_map,
        transcript=transcript,
        metrics=summary,
        tags=tags,
        occurrences=positions,
        layout_pdf=layout[0] if layout else None,
        structure=layout[1] if layout else None,
    )


def restore(markdown: str, encrypted_map: bytes, key: str) -> str:
    """Original text from the pseudonymized Markdown and its map.

    Raises:
        VaultError: Wrong key, invalid or tampered map, modified text.
    """
    return vault.restore(markdown, vault.decrypt_map(encrypted_map, key))


def restore_detail(
    markdown: str, encrypted_map: bytes, key: str, cfg: PipelineConfig
) -> dict[str, Any]:
    """Original text and position of every tag in the redacted and restored text, with the category
    (for the comparison in the web UI).

    Raises:
        VaultError: Wrong key, invalid or tampered map, modified text.
    """
    text, positions = vault.restore_detail(
        markdown, vault.decrypt_map(encrypted_map, key)
    )
    for position in positions:
        position["category"] = tag_category(position["replacement"], cfg)
    return {"text": text, "occurrences": positions}


def restore_pdf(document: bytes, encrypted_map: bytes, key: str) -> bytes:
    """Original PDF (layout, text and tables, without images) from the censored layout PDF, or its
    structure JSON, and the map.

    Raises:
        VaultError: Wrong key, invalid or tampered map.
        StructureError: No structure in the PDF or in the map, or they do not match.
    """
    content = vault.decrypt_map(encrypted_map, key)
    data: bytes | None = document
    if document.startswith(b"%PDF"):
        try:
            data = attachment(document, structure.ATTACHMENT)
        except ValueError as exc:
            raise structure.StructureError("PDF non leggibile") from exc
    if data is None:
        raise structure.StructureError("Il PDF non contiene la struttura del documento")
    original = structure.restore(structure.load(data), content)
    return render_pdf(original, highlight=False)
