"""Models file: the VLM catalog and the default model of each phase (``resources/models.yaml``).

The same file drives the download before the service starts (``model_store``: every model of the
catalog) and the choice at runtime (a request may pick, for each phase, a model of the catalog).
"""

import functools
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from anonymization_pipeline.pipeline_config.resources import parse_yaml, read_yaml

FILE = "models.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Entry(_Strict):
    name: str = Field(min_length=1)
    context: int = Field(ge=1024)
    vision: (
        bool  # accepts images: required to extract scans, else detection gets text only
    )


class _PhaseModels(_Strict):
    extraction: str = Field(min_length=1)
    detection: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class ModelsFile(_Strict):
    models: list[_Entry] = Field(min_length=1)
    phases: _PhaseModels

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        """Files written when redaction had its own model still load: the key is ignored."""
        if isinstance(data, dict) and isinstance(data.get("phases"), dict):
            data = {
                **data,
                "phases": {k: v for k, v in data["phases"].items() if k != "redaction"},
            }
        return data

    @model_validator(mode="after")
    def _consistency(self) -> "ModelsFile":
        names = [m.name for m in self.models]
        if twice := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"models listed twice: {', '.join(twice)}")
        for phase, name in self.phases.model_dump().items():
            if name not in names:
                raise ValueError(f"phases.{phase}: {name} is not in models")
        return self


@functools.cache
def read_models_file(path: str = "") -> ModelsFile:
    """The models file at ``path``, or the packaged one when ``path`` is empty.

    Read once per path: the catalog cannot change while the service runs, and every request
    must see the same one as the service configuration.

    Raises:
        ValueError: If the file is missing, not valid YAML or not valid.
    """
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Models file not readable: {path}") from exc
        data = parse_yaml(text, path)
    else:
        data = read_yaml(FILE)
    try:
        return ModelsFile.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid models file {path or FILE}: {exc}") from exc


def models_layer(path: str = "") -> dict:
    """The pipeline configuration keys that come from the models file."""
    models = read_models_file(path)
    return {
        "models": {
            m.name: {"context": m.context, "vision": m.vision} for m in models.models
        },
        "phases": {
            phase: {"model": name} for phase, name in models.phases.model_dump().items()
        },
    }
