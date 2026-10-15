"""Access to the YAML files shipped in ``anonymization_pipeline/resources``."""

from importlib.resources import files
from typing import Any

import yaml

_PACKAGE = "anonymization_pipeline.resources"


def read_yaml(name: str) -> dict[str, Any]:
    """Parse a resource file (``detectors/rules.yaml`` ...) into a dictionary.

    Raises:
        ValueError: If the file is not valid YAML or not a mapping.
    """
    return parse_yaml(files(_PACKAGE).joinpath(name).read_text(encoding="utf-8"), name)


def parse_yaml(text: str, source: str) -> dict[str, Any]:
    """Parse YAML text that must hold a mapping (an empty document is ``{}``)."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid configuration in {source}: expected a YAML mapping")
    return data
