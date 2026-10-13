"""Scoring of an anonymization result against the ground truth of its document.

Units:

- **values** (ground truth entries, one per value and category): recall, leaks, category;
- **occurrences** (what the service redacted, from ``occurrences`` of the transcript): precision.

A value is *expected* when its category, or the generic category of its family, is active in the
profile (an inactive specific category falls back to its generic one, as in the service). It is
*extracted* when it is found in the transcript; values the extraction lost are counted apart and
left out of the detection metrics. It is *detected* when the redacted spans cover at least half of
one of its occurrences, *protected* when no clear-text copy is left in the redacted Markdown
beyond the copies the ground truth leaves unmarked
(values shorter than 3 letters and digits: when as many occurrences as the ground truth mentions
are fully covered).
"""

import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from anonval.settings import Thresholds
from anonval.text import alnum_count, find
from anonymization_pipeline.pipeline_config.categories import registry

DETECTED = 0.5
SHORT = 3
# replacements of tag mode (phases.redaction.tag_format) and of redaction mode
TAG = re.compile(r"\[[A-Z_]+_\d+\]|\u2588+")
# separator before pages 2 and following (output.page_separator)
PAGE = re.compile(r"<!-- pagina (\d+) -->")


@dataclass
class ValueOutcome:
    value: str
    category: str
    group: str
    locations: list[str]
    expected: bool
    extracted: bool = False
    detected: bool = False
    protected: bool = False
    category_exact: bool = False
    category_family: bool = False
    predicted: list[str] = field(default_factory=list)
    # detectors that redacted it somewhere
    sources: list[str] = field(default_factory=list)
    # pages where it is
    pages: list[int] = field(default_factory=list)
    # pages where it is left in clear
    leak_pages: list[int] = field(default_factory=list)
    # censored layout PDF (layout: true): None = not checked (no PDF, value too short)
    layout_protected: bool | None = None
    layout_leak_pages: list[int] = field(default_factory=list)


@dataclass
class Hit:
    """A redaction the ground truth does not justify (false positive, violated negative)."""

    text: str
    category: str
    sources: list[str]
    page: int


@dataclass
class DocScore:
    id: str
    type: str
    difficulty: str
    status: str
    error: str | None = None
    values: list[ValueOutcome] = field(default_factory=list)
    tp: int = 0
    tp_category: int = 0
    false_positives: list[Hit] = field(default_factory=list)
    disabled_hits: int = 0
    negatives_violated: list[Hit] = field(default_factory=list)
    residuals: int = 0
    verification: str | None = None
    pages_docling: int = 0
    pages_vlm: list[str] = field(default_factory=list)
    hallucinations: int = 0
    eponyms_discarded: int = 0
    duration_s: float | None = None
    pipeline_s: float | None = None
    # the censored layout PDF was produced and checked
    layout_checked: bool = False
    # the layout PDF was requested but not produced or not readable
    layout_missing: bool = False

    @property
    def leaked(self) -> list[str]:
        return [
            v.value
            for v in self.values
            if v.expected and v.extracted and not v.protected
        ]

    @property
    def layout_leaked(self) -> list[str]:
        return [v.value for v in self.values if v.layout_protected is False]


def active_categories(
    base: Iterable[str], enable: list[str] | None, disable: list[str] | None
) -> set[str]:
    """Categories active for a profile: the server's active ones, plus ``enable``, minus
    ``disable`` (groups or categories; ``disable`` wins).

    Raises:
        ValueError: On an unknown group or category.
    """
    reg = registry()
    active = set(base) | set(reg.expand(enable or []))
    return active - set(reg.expand(disable or []))


def _expected(category: str, active: set[str]) -> bool:
    return category in active or registry().family(category) in active


def _coverage(
    span: tuple[int, int], redacted: list[tuple[int, int]], text: str
) -> float:
    start, end = span
    total = alnum_count(text[start:end])
    if not total:
        return 0.0
    covered = sum(
        1
        for i in range(start, end)
        if text[i].isalnum() and any(s <= i < e for s, e in redacted)
    )
    return covered / total


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def without_replacements(markdown: str, tags: list[dict[str, Any]] | None) -> str:
    """Redacted Markdown with the replacements blanked: a tag (``[GRAVIDANZA_1]``) or a
    pseudonym must never count as a clear-text copy of a value."""
    if tags is None:
        return TAG.sub(lambda m: " " * len(m.group(0)), markdown)
    chars = list(markdown)
    for tag in tags:
        for i in range(tag["start"], min(tag["end"], len(chars))):
            chars[i] = " "
    return "".join(chars)


def _check_layout(score: DocScore, gold: dict[str, Any], pages: list[str]) -> None:
    """Clear-text copies in the text of the censored layout PDF (replacements already blanked).

    Same rule as the Markdown for values of at least 3 letters and digits: no more copies than the
    ground truth leaves unmarked. Shorter values ("F", "45") are not checked: without offsets
    they cannot be told apart from unrelated text.
    """
    unmarked = {
        (e["value"], e["category"]): e.get("unmarked", 0) for e in gold["entities"]
    }
    for v in score.values:
        if not (v.expected and v.extracted) or alnum_count(v.value) < SHORT:
            continue
        found = [
            (i + 1, n)
            for i, page in enumerate(pages)
            if (n := len(find(page, v.value)))
        ]
        v.layout_protected = sum(n for _, n in found) <= unmarked[(v.value, v.category)]
        if not v.layout_protected:
            v.layout_leak_pages = [page for page, _ in found]


def page_of(text: str, offset: int) -> int:
    """Page of ``offset`` in a transcript or redacted Markdown (from the page separators)."""
    page = 1
    for match in PAGE.finditer(text, 0, offset):
        page = int(match.group(1))
    return page


def _sources(result: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    """(replacement, category) -> detectors that found the values of that group."""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entity in (result.get("report") or {}).get("entities") or []:
        out[(entity["replacement"], entity["category"])].update(
            entity.get("sources") or []
        )
    return {key: sorted(found) for key, found in out.items()}


def error_score(gold: dict[str, Any], status: str, error: str | None) -> DocScore:
    """Score of a document without a result (rejected, error, timeout)."""
    return DocScore(gold["id"], gold["type"], gold["difficulty"], status, error)


def score_document(
    gold: dict[str, Any], job: dict[str, Any], active: set[str]
) -> DocScore:
    """Compare a completed job (``status``, ``duration_s``, ``result``) with the ground truth.

    Args:
        gold: Ground truth of the document (``build.ground_truth``).
        job: Saved job: ``result`` is the JSON of ``GET /jobs/{id}/result`` with the transcript.
        active: Categories active in the profile.
    """
    reg = registry()
    result = job["result"]
    transcript: str = result.get("transcript") or ""
    markdown = without_replacements(result.get("markdown") or "", result.get("tags"))
    occurrences = [
        o
        for o in result.get("occurrences") or []
        if o.get("original_start") is not None
    ]
    spans = [(o["original_start"], o["original_end"]) for o in occurrences]
    by_group = _sources(result)
    found_by = [
        by_group.get((o.get("replacement", ""), o["category"]), []) for o in occurrences
    ]
    score = DocScore(
        gold["id"],
        gold["type"],
        gold["difficulty"],
        "completed",
        duration_s=job.get("duration_s"),
    )
    instances: list[tuple[tuple[int, int], ValueOutcome]] = []
    for entity in gold["entities"]:
        outcome = ValueOutcome(
            entity["value"],
            entity["category"],
            entity["group"],
            list(entity["locations"]),
            _expected(entity["category"], active),
        )
        found = find(transcript, entity["value"])
        outcome.extracted = bool(found)
        # where it is: transcript pages, or the PDF pages when the extraction lost it
        outcome.pages = sorted({page_of(transcript, s) for s, _ in found}) or list(
            entity.get("pages") or []
        )
        coverages = [_coverage(span, spans, transcript) for span in found]
        outcome.detected = any(c >= DETECTED for c in coverages)
        if alnum_count(entity["value"]) >= SHORT:
            # copies the ground truth leaves unmarked (the same word as plain text) may stay
            clear = find(markdown, entity["value"])
            outcome.protected = outcome.extracted and len(clear) <= entity.get(
                "unmarked", 0
            )
            clear_pages = {page_of(markdown, s) for s, _ in clear}
        else:
            # short values ("F", "45") also match unrelated text ("C.F."): enough covered
            # occurrences, at most as many as the mentions in the document
            needed = min(entity.get("mentions", 1), len(found))
            outcome.protected = outcome.extracted and (
                sum(1 for c in coverages if c >= 0.99) >= needed
            )
            clear_pages = {
                page_of(transcript, span[0])
                for span, c in zip(found, coverages, strict=True)
                if c < 0.99
            }
        if outcome.extracted and not outcome.protected:
            outcome.leak_pages = sorted(clear_pages)
        sources: set[str] = set()
        for span in found:
            instances.append((span, outcome))
            for o, o_span, by in zip(occurrences, spans, found_by, strict=True):
                if _overlaps(span, o_span):
                    sources.update(by)
                    if o["category"] not in outcome.predicted:
                        outcome.predicted.append(o["category"])
        outcome.sources = sorted(sources)
        outcome.category_exact = outcome.category in outcome.predicted
        outcome.category_family = any(
            reg.family(c) == reg.family(outcome.category) for c in outcome.predicted
        )
        score.values.append(outcome)

    layout_pages = result.get("layout_pages")
    if layout_pages is not None:
        score.layout_checked = True
        _check_layout(
            score, gold, [without_replacements(p, None) for p in layout_pages]
        )
    elif (
        result.get("layout_error")
        or (result.get("report") or {}).get("layout") == "unavailable"
    ):
        score.layout_missing = True

    negatives = [
        (n["value"], {reg.family(c) for c in n["forbidden_categories"]})
        for n in gold.get("negatives", [])
    ]
    for o, o_span, by in zip(occurrences, spans, found_by, strict=True):
        hit = [v for span, v in instances if _overlaps(span, o_span)]
        expected = [v for v in hit if v.expected]
        page = page_of(transcript, o_span[0])
        if not hit:
            score.false_positives.append(Hit(o["original"], o["category"], by, page))
        elif not expected:
            score.disabled_hits += 1
        else:
            score.tp += 1
            if any(
                reg.family(v.category) == reg.family(o["category"]) for v in expected
            ):
                score.tp_category += 1
        # a negative inside a redacted value of the same family (the surname Neri of a
        # relative) is a correct redaction, not a violation
        family = reg.family(o["category"])
        if any(reg.family(v.category) == family for v in hit):
            continue
        for value, families in negatives:
            if family in families and find(o["original"], value):
                score.negatives_violated.append(Hit(value, o["category"], by, page))

    report = result.get("report") or {}
    verification = report.get("verification") or {}
    score.verification = verification.get("outcome")
    score.residuals = len(verification.get("residuals") or [])
    extraction = report.get("extraction") or {}
    score.pages_docling = len(extraction.get("docling") or [])
    score.pages_vlm = [p.get("reason", "") for p in extraction.get("vlm") or []]
    detection = report.get("detection") or {}
    score.hallucinations = detection.get("hallucinations_discarded") or 0
    score.eponyms_discarded = detection.get("eponyms_discarded") or 0
    score.pipeline_s = (report.get("times_s") or {}).get("total")
    return score


# --- aggregation -------------------------------------------------------------------------------


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def value_stats(values: Iterable[ValueOutcome]) -> dict[str, Any]:
    """Counts and rates of a set of expected values."""
    expected = [v for v in values if v.expected]
    extracted = [v for v in expected if v.extracted]
    detected = [v for v in extracted if v.detected]
    leaks = [v for v in extracted if not v.protected]
    return {
        "attesi": len(extracted),
        "non_estratti": len(expected) - len(extracted),
        "rilevati": len(detected),
        "fughe": len(leaks),
        "categoria_esatta": sum(1 for v in detected if v.category_exact),
        "categoria_famiglia": sum(1 for v in detected if v.category_family),
        "recall": _ratio(len(detected), len(extracted)),
        "tasso_fuga": _ratio(len(leaks), len(extracted)),
    }


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 4)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=20, method="inclusive")[-1], 1)


def _overall(docs: list[DocScore]) -> dict[str, Any]:
    done = [d for d in docs if d.status == "completed"]
    values = value_stats(v for d in done for v in d.values)
    tp, fp = sum(d.tp for d in done), sum(len(d.false_positives) for d in done)
    precision = _ratio(tp, tp + fp)
    precision_category = _ratio(sum(d.tp_category for d in done), tp + fp)
    verifier = {
        "fughe_segnalate": 0,
        "fughe_non_segnalate": 0,
        "falsi_allarmi": 0,
        "senza_fughe": 0,
    }
    for d in done:
        leak, flagged = bool(d.leaked), d.residuals > 0
        key = (
            ("fughe_segnalate" if flagged else "fughe_non_segnalate")
            if leak
            else ("falsi_allarmi" if flagged else "senza_fughe")
        )
        verifier[key] += 1
    vlm_reasons: dict[str, int] = defaultdict(int)
    for d in done:
        for reason in d.pages_vlm:
            vlm_reasons[reason] += 1
    durations = [d.duration_s for d in done if d.duration_s is not None]
    checked = [d for d in done if d.layout_checked]
    missing = sum(1 for d in done if d.layout_missing)
    layout = None
    if checked or missing:
        verified = [
            v for d in checked for v in d.values if v.layout_protected is not None
        ]
        leaks = sum(1 for v in verified if v.layout_protected is False)
        layout = {
            "documenti_verificati": len(checked),
            "pdf_non_prodotti": missing,
            "valori_verificati": len(verified),
            "fughe": leaks,
            "tasso_fuga": _ratio(leaks, len(verified)),
            "documenti_con_fughe": sum(1 for d in checked if d.layout_leaked),
        }
    return {
        "documenti": {
            "totali": len(docs),
            "completati": len(done),
            "respinti": sum(1 for d in docs if d.status == "rejected"),
            "errori": sum(1 for d in docs if d.status not in ("completed", "rejected")),
        },
        "valori": values,
        "occorrenze": {
            "censurate": tp + fp,
            "corrette": tp,
            "categoria_corretta": sum(d.tp_category for d in done),
            "falsi_positivi": fp,
            "categorie_disattivate": sum(d.disabled_hits for d in done),
        },
        "recall": values["recall"],
        "tasso_fuga": values["tasso_fuga"],
        "precision": precision,
        "precision_categoria": precision_category,
        "f1": _f1(precision, values["recall"]),
        "documenti_con_fughe": sum(1 for d in done if d.leaked),
        "layout": layout,
        "negativi_violati": sum(len(d.negatives_violated) for d in done),
        "verificatore": verifier,
        "estrazione": {
            "pagine_docling": sum(d.pages_docling for d in done),
            "pagine_vlm": dict(vlm_reasons),
        },
        "allucinazioni_scartate": sum(d.hallucinations for d in done),
        "eponimi_scartati": sum(d.eponyms_discarded for d in done),
        "tempi_s": {
            "medio": round(statistics.fmean(durations), 1) if durations else None,
            "p95": _p95(durations),
            "totale": round(sum(durations), 1),
        },
    }


def _grouped(
    docs: list[DocScore], key: Callable[[ValueOutcome], Iterable[str]]
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[ValueOutcome]] = defaultdict(list)
    for d in docs:
        if d.status != "completed":
            continue
        for v in d.values:
            for k in key(v):
                buckets[k].append(v)
    return {k: value_stats(vs) for k, vs in sorted(buckets.items())}


def summarize(docs: list[DocScore]) -> dict[str, Any]:
    """Metrics of a profile: overall, per category, group, type x difficulty, layout position."""
    by_category = _grouped(docs, lambda v: [v.category])
    fp_by_category: dict[str, int] = defaultdict(int)
    for d in docs:
        for hit in d.false_positives:
            fp_by_category[hit.category] += 1
    for category, n in fp_by_category.items():
        by_category.setdefault(category, value_stats([]))["falsi_positivi"] = n
    for stats in by_category.values():
        stats.setdefault("falsi_positivi", 0)
    by_type: dict[str, dict[str, Any]] = {}
    for doc_type in sorted({d.type for d in docs}):
        by_type[doc_type] = {
            difficulty: value_stats(
                v
                for d in docs
                if d.type == doc_type
                and d.difficulty == difficulty
                and d.status == "completed"
                for v in d.values
            )
            for difficulty in ("facile", "media", "complessa")
            if any(d.type == doc_type and d.difficulty == difficulty for d in docs)
        }
    return {
        "overall": _overall(docs),
        "by_category": dict(sorted(by_category.items())),
        "by_group": _grouped(docs, lambda v: [v.group]),
        "by_type_difficulty": by_type,
        "by_difficulty": {
            difficulty: value_stats(
                v
                for d in docs
                if d.difficulty == difficulty and d.status == "completed"
                for v in d.values
            )
            for difficulty in ("facile", "media", "complessa")
            if any(d.difficulty == difficulty for d in docs)
        },
        "by_location": _grouped(docs, lambda v: v.locations),
    }


def verdict(overall: dict[str, Any], thresholds: Thresholds) -> dict[str, bool | None]:
    """Each threshold: True met, False missed, None not measurable (no value)."""

    def check(value: float | None, limit: float, low: bool) -> bool | None:
        if value is None:
            return None
        return value >= limit if low else value <= limit

    return {
        "recall_min": check(overall["recall"], thresholds.recall_min, True),
        "tasso_fuga_max": check(
            overall["tasso_fuga"], thresholds.tasso_fuga_max, False
        ),
        "precision_min": check(overall["precision"], thresholds.precision_min, True),
        "f1_min": check(overall["f1"], thresholds.f1_min, True),
        # the leak threshold applies to the censored layout PDF too, when it was checked
        "tasso_fuga_layout_max": check(
            (overall.get("layout") or {}).get("tasso_fuga"),
            thresholds.tasso_fuga_max,
            False,
        ),
    }
