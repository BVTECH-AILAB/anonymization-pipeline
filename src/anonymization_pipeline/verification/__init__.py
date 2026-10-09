"""Verification: the VLM reads the redacted Markdown again (comparing it with the clear Markdown)
and reports the sensitive data left in clear text. The fix/report/block loop is in ``pipeline.py``.

The residuals are anchored to the redacted text outside the tags and the redaction blocks: a model
that "invents" residuals moves nothing; an instruction hidden in the document can at most hide a
residual, never make a value appear.
"""

from typing import Any

from anonymization_pipeline.detection import Outcome, find_values
from anonymization_pipeline.domain import Document, Residual
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.text import replacement_spans
from anonymization_pipeline.vlm import VLMClient, gather_or_cancel


class UnredactableContent(Exception):
    """The document cannot be returned.

    Residuals after the fixes, verification impossible or invariant broken. It holds no values,
    only categories and pages.
    """

    def __init__(self, reason: str, residuals: list[Residual] | None = None) -> None:
        self.reason = reason
        self.residuals = sorted({(r.category, r.page) for r in residuals or []})
        detail = ", ".join(f"{c} (pagina {p})" for c, p in self.residuals)
        super().__init__(
            f"Contenuto non censurabile: {reason}" + (f": {detail}" if detail else "")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "residuals": [{"category": c, "page": p} for c, p in self.residuals],
        }


async def verify(
    doc: Document, redacted: list[str], client: VLMClient, cfg: PipelineConfig
) -> Outcome:
    """Sensitive data still in clear text, as entities (source "verification") of the redacted text."""
    passes = cfg.passes("verification")
    jobs = []
    for page, text in zip(doc.pages, redacted, strict=True):
        if not text.strip():
            continue
        exclude = replacement_spans(text, cfg)
        jobs += [
            find_values(client, cfg, "verification", page, text, cats, exclude)
            for cats in passes
        ]
    outcome = Outcome()
    for result in await gather_or_cancel(jobs):
        outcome.add(result)
    return outcome
