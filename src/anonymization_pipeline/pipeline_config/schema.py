"""Pipeline configuration schema: types, ranges, allowed keys.

Every section rejects unknown keys (``extra="forbid"``): a typo in the user file is an error,
not an ignored value.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from anonymization_pipeline.pipeline_config.categories import registry


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ingest(_Section):
    """Limits and conversion of the uploaded file."""

    max_pages: int = Field(ge=1, le=1000)
    max_pixels: int = Field(
        ge=1
    )  # per page or image, at render_dpi: decompression bombs
    render_dpi: int = Field(ge=50, le=600)
    image_dpi: int = Field(
        ge=50, le=1200
    )  # images without a resolution in their metadata


class Anchoring(_Section):
    min_length: int = Field(ge=1)
    case_insensitive_over: int = Field(ge=0)
    fuzzy: float = Field(ge=0, le=1)


class Detectors(_Section):
    """Text detectors in union (one is enough to flag a value) and the common eponym filter."""

    rules: bool
    labels: bool
    eponyms: bool


class Propagation(_Section):
    """Search of every value found in the whole document, also in its other forms."""

    names: bool  # single name tokens, initials, inverted and glued full names
    min_token: int = Field(ge=2)


class Detection(_Section):
    anchoring: Anchoring
    detectors: Detectors
    propagation: Propagation


class Protection(_Section):
    """Groups and categories a request can never switch off."""

    protected: list[str] = Field(min_length=1)


class PipelineConfig(_Section):
    """Effective, validated configuration of one pipeline run."""

    ingest: Ingest
    detection: Detection
    categories: dict[str, bool]
    protection: Protection

    @model_validator(mode="after")
    def _consistency(self) -> "PipelineConfig":
        reg = registry()
        if unknown := sorted(set(self.categories) - set(reg.descriptions)):
            raise ValueError(f"unknown categories: {', '.join(unknown)}")
        if missing := [c for c in reg.descriptions if c not in self.categories]:
            raise ValueError(
                f"categories without an on/off value: {', '.join(missing)}"
            )
        if off := [c for c in self.protected() if not self.categories[c]]:
            raise ValueError(f"protected categories switched off: {', '.join(off)}")
        detectors = self.detection.detectors
        if not (detectors.rules or detectors.labels):
            raise ValueError("detection.detectors: at least one detector must be on")
        return self

    def active(self) -> list[str]:
        """Active categories, in registry order."""
        return [c for c in registry().descriptions if self.categories.get(c)]

    def protected(self) -> list[str]:
        """Categories of the protection floor, in registry order.

        Raises:
            ValueError: On an unknown group or category in ``protection.protected``.
        """
        return registry().expand(self.protection.protected)
