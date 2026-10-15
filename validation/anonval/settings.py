"""Validation configuration (``validation/config.yaml``): server, dataset, thresholds, profiles.

A profile is a named set of request parameters of ``POST /anonymizations``. ``comune`` holds the
parameters shared by every profile; a profile overrides them field by field.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from anonval.spec import Difficulty, DocType
from anonymization_pipeline.pipeline_config.categories import registry


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Server(_Model):
    base_url: str = "http://localhost:8000"
    poll_interval_s: float = Field(default=1, gt=0, le=60)
    timeout_documento_s: float = Field(default=1800, gt=0)


class Dataset(_Model):
    percorso: str = "dataset"
    tipi: list[DocType] = []
    difficolta: list[Difficulty] = []
    documenti: list[str] = []


class Thresholds(_Model):
    recall_min: float = Field(default=0.95, ge=0, le=1)
    tasso_fuga_max: float = Field(default=0.05, ge=0, le=1)
    precision_min: float = Field(default=0.80, ge=0, le=1)


class Parameters(_Model):
    """Parameters of ``POST /anonymizations``; None = the server default."""

    enable: list[str] | None = None
    disable: list[str] | None = None

    @field_validator("enable", "disable")
    @classmethod
    def _known_categories(cls, value: list[str] | None) -> list[str] | None:
        if value:
            registry().expand(value)
        return value

    def form(self) -> dict[str, str]:
        """Multipart fields of the request (only the parameters that are set)."""
        return {
            name: ",".join(value)
            for name, value in self.model_dump(exclude_none=True).items()
        }


class Profile(Parameters):
    nome: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    descrizione: str = ""


class Config(_Model):
    server: Server = Server()
    dataset: Dataset = Dataset()
    soglie: Thresholds = Thresholds()
    comune: Parameters = Parameters()
    profili: list[Profile] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> "Config":
        names = [p.nome for p in self.profili]
        if len(names) != len(set(names)):
            raise ValueError("profile names must be unique")
        return self

    def effective(self, profile: Profile) -> Parameters:
        """Parameters of a profile: the common ones overridden by the profile's own."""
        own = profile.model_dump(exclude_none=True, exclude={"nome", "descrizione"})
        return Parameters.model_validate(
            {**self.comune.model_dump(exclude_none=True), **own}
        )


def load_config(path: Path) -> Config:
    """Validated configuration; relative paths are resolved against the file's folder.

    Raises:
        ValueError: Unreadable file or invalid content (pydantic's ``ValidationError`` included).
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"configurazione non leggibile: {path}: {exc}") from exc
    config = Config.model_validate(data)
    dataset = Path(config.dataset.percorso)
    if not dataset.is_absolute():
        config.dataset.percorso = str((path.parent / dataset).resolve())
    return config
