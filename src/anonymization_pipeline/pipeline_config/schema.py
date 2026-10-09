"""Pipeline configuration schema: types, ranges, allowed keys.

Every section rejects unknown keys (``extra="forbid"``): a typo in the user file is an error,
not an ignored value. The phases have no ``model`` key: there is one model, in ``model``.
"""

import string
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from anonymization_pipeline.pipeline_config.categories import registry

PHASES = ("extraction", "detection", "redaction", "verification")
Phase = Literal["extraction", "detection", "redaction", "verification"]

# prompt placeholders: phase -> key -> (allowed, required)
PROMPTS: dict[str, dict[str, tuple[set[str], set[str]]]] = {
    "extraction": {"system": ({"visual_elements"}, set()), "user": (set(), set())},
    "detection": {
        "system": (set(), set()),
        "user": ({"categories", "text", "page"}, {"categories", "text"}),
    },
    "redaction": {"system": (set(), set()), "user": ({"values"}, {"values"})},
    "verification": {
        "system": (set(), set()),
        "user": ({"categories", "text", "page"}, {"categories", "text"}),
    },
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Backend(_Section):
    kind: Literal["ollama", "openai"]
    url: str = Field(min_length=1)
    api_key: str | None = None
    concurrency: int = Field(ge=1)
    retries: int = Field(ge=0)
    retry_wait_s: float = Field(ge=0)
    pull_missing: bool
    pull_timeout_s: float = Field(gt=0)
    health_timeout_s: float = Field(gt=0)


class Model(_Section):
    name: str = Field(min_length=1)
    context: int = Field(ge=1024)
    temperature: float = Field(ge=0)
    seed: int | None = None
    think: bool | str | None = None
    keep_alive: str | int | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class Images(_Section):
    pdf_dpi: int = Field(ge=50, le=600)
    max_side_px: int = Field(ge=256)
    format: Literal["png", "jpeg"]
    jpeg_quality: int = Field(ge=1, le=100)
    max_pixels: int = Field(ge=1)
    max_pages: int = Field(ge=1)


class Text(_Section):
    encodings: list[str] = Field(min_length=1)
    max_block_chars: int = Field(ge=200)


class _Phase(_Section):
    timeout_s: float = Field(gt=0)
    max_tokens: int = Field(ge=16)


class NativePdf(_Section):
    """PDF pages with a native text layer: extracted with docling instead of the VLM."""

    engine: Literal["docling", "vlm"]  # vlm = always the VLM
    min_chars: int = Field(ge=0)  # below: scanned (or almost empty) page
    max_unreadable: float = Field(
        ge=0, le=1
    )  # allowed fraction of undecodable characters
    max_image_fraction: float = Field(ge=0, le=1)
    min_contrast: int = Field(
        ge=0, le=255
    )  # below: text not visible in the rendered page
    tables: bool  # table structure (TableFormer model)
    models_dir: str = Field(min_length=1)
    threads: int | None = Field(default=None, ge=1)


class Extraction(_Phase):
    native_pdf: NativePdf
    visual_elements: dict[str, str]


class Anchoring(_Section):
    min_length: int = Field(ge=1)
    case_insensitive_over: int = Field(ge=0)
    fuzzy: float = Field(ge=0, le=1)


class NerFilters(_Section):
    min_length: int = Field(ge=1)
    require_capital: list[str]  # proper-name categories: at least one capitalized word
    false_positives: list[
        str
    ]  # words often mistaken for proper names (labels, headings)


class Ner(_Section):
    enabled: bool
    model: str = Field(min_length=1)  # Hugging Face hub name or local folder
    revision: str | None = None
    threshold: float = Field(ge=0, le=1)
    window_words: int = Field(ge=10)
    overlap_words: int = Field(ge=0)
    threads: int | None = Field(default=None, ge=1)
    label_map: dict[str, str]  # model label -> category
    filters: NerFilters


class Detectors(_Section):
    """Detectors in union: one is enough to flag a value."""

    vlm: bool
    rules: bool
    labels: bool
    eponyms: bool  # common filter
    ner: Ner


class Detection(_Phase):
    use_image: bool
    passes: list[list[str]] = Field(min_length=1)
    anchoring: Anchoring
    detectors: Detectors


class Redaction(_Phase):
    mode: Literal["pseudonym", "tag", "redaction"]
    coreference: Literal["vlm", "exact"]
    tag_format: str
    redaction_symbol: str = Field(min_length=1)

    @field_validator("tag_format")
    @classmethod
    def _tag(cls, value: str) -> str:
        if "{category}" not in value or "{n}" not in value:
            raise ValueError("tag_format must contain {category} and {n}")
        try:
            value.format(category="X", n=1)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"invalid tag_format: {exc}") from exc
        return value


class Verification(_Phase):
    mode: Literal["off", "report", "fix", "block"]
    max_iterations: int = Field(ge=1, le=10)
    use_image: bool
    required: bool
    passes: list[list[str]] | None = None


class Phases(_Section):
    extraction: Extraction
    detection: Detection
    redaction: Redaction
    verification: Verification


class Output(_Section):
    page_separator: str
    save_transcript: bool
    save_metrics: bool


class PipelineConfig(_Section):
    """Effective, validated configuration of one pipeline run."""

    backend: Backend
    model: Model
    images: Images
    text: Text
    phases: Phases
    categories: dict[str, bool]
    output: Output
    prompts: dict[str, dict[str, str]]

    @model_validator(mode="after")
    def _consistency(self) -> "PipelineConfig":
        reg = registry()
        if unknown := sorted(set(self.categories) - set(reg.descriptions)):
            raise ValueError(f"unknown categories: {', '.join(unknown)}")
        if missing := [c for c in reg.descriptions if c not in self.categories]:
            raise ValueError(
                f"categories without an on/off value: {', '.join(missing)}"
            )
        for entries in self.phases.detection.passes + (
            self.phases.verification.passes or []
        ):
            reg.expand(entries)
        detectors = self.phases.detection.detectors
        if not (
            detectors.vlm
            or detectors.rules
            or detectors.labels
            or detectors.ner.enabled
        ):
            raise ValueError(
                "phases.detection.detectors: at least one detector must be on"
            )
        ner = detectors.ner
        unknown = sorted(
            (set(ner.label_map.values()) | set(ner.filters.require_capital))
            - set(reg.descriptions)
        )
        if unknown:
            raise ValueError(
                f"phases.detection.detectors.ner: unknown categories {', '.join(unknown)}"
            )
        _check_prompts(self.prompts)
        return self

    def active(self) -> list[str]:
        """Active categories, in registry order."""
        return [c for c in registry().descriptions if self.categories.get(c)]

    def passes(self, phase: Literal["detection", "verification"]) -> list[list[str]]:
        """Category passes for detection or verification: only active categories, no empty pass.

        Active categories not named in any pass go into a final pass, so none is left unchecked.
        """
        verification = self.phases.verification.passes
        entries = (
            verification
            if phase == "verification" and verification
            else self.phases.detection.passes
        )
        reg = registry()
        active = set(self.active())
        out: list[list[str]] = []
        seen: set[str] = set()
        for entry in entries:
            categories = [c for c in reg.expand(entry) if c in active and c not in seen]
            seen.update(categories)
            if categories:
                out.append(categories)
        if rest := [c for c in self.active() if c not in seen]:
            out.append(rest)
        return out


def _check_prompts(prompts: dict[str, dict[str, str]]) -> None:
    for phase, keys in PROMPTS.items():
        if phase not in prompts:
            raise ValueError(f"missing prompts for phase {phase}")
        if unknown := set(prompts[phase]) - set(keys):
            raise ValueError(
                f"prompts.{phase}: unknown keys {', '.join(sorted(unknown))}"
            )
        for key, (allowed, required) in keys.items():
            text = prompts[phase].get(key)
            if text is None:
                raise ValueError(f"prompts.{phase}.{key} is missing")
            template = string.Template(text)
            if not template.is_valid():
                raise ValueError(
                    f"prompts.{phase}.{key}: invalid placeholder (write $$ for a literal $)"
                )
            used = set(template.get_identifiers())
            if used - allowed:
                raise ValueError(
                    f"prompts.{phase}.{key}: unknown placeholders {', '.join(sorted(used - allowed))} "
                    f"(allowed: {', '.join(sorted(allowed)) or 'none'})"
                )
            if required - used:
                raise ValueError(
                    f"prompts.{phase}.{key}: missing placeholders {', '.join(sorted(required - used))}"
                )
    if unknown := set(prompts) - set(PROMPTS):
        raise ValueError(f"prompts for unknown phases: {', '.join(sorted(unknown))}")
