"""Layered loading: default.yaml (+ prompts) -> user file -> models file -> settings -> request
overrides."""

import copy
import functools
import string
from pathlib import Path
from typing import Any

from anonymization_pipeline.config import Settings
from anonymization_pipeline.pipeline_config.categories import registry
from anonymization_pipeline.pipeline_config.models import models_layer
from anonymization_pipeline.pipeline_config.resources import parse_yaml, read_yaml
from anonymization_pipeline.pipeline_config.schema import PHASES, PipelineConfig

# keys owned by the ANONPIP_* settings -> Settings attribute
SETTINGS_KEYS = {
    "backend.kind": "vlm_backend",
    "backend.url": "vlm_url",
    "backend.api_key": "vlm_api_key",
    "backend.concurrency": "vlm_concurrency",
    "backend.pull_missing": "vlm_pull_missing",
    "phases.redaction.mode": "default_mode",
    "phases.verification.mode": "default_verification",
    "phases.extraction.native_pdf.engine": "default_extraction",
    "phases.extraction.native_pdf.models_dir": "docling_models_dir",
}
# detectors and filters a request may switch on or off -> configuration key
DETECTOR_KEYS = {
    "vlm": "phases.detection.detectors.vlm",
    "rules": "phases.detection.detectors.rules",
    "labels": "phases.detection.detectors.labels",
    "ner": "phases.detection.detectors.ner.enabled",
    "eponyms": "phases.detection.detectors.eponyms",
}
# keys owned by the models file (ANONPIP_MODELS_FILE)
MODELS_KEYS = ["models", *(f"phases.{phase}.model" for phase in PHASES)]


def merge(base: dict, above: dict) -> dict:
    """Deep merge: ``above`` wins, nested mappings are merged key by key."""
    out = copy.deepcopy(base)
    for key, value in (above or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _set(target: dict, dotted: str, value: Any) -> None:
    *sections, last = dotted.split(".")
    for section in sections:
        node = target.get(section)
        if not isinstance(node, dict):
            node = target[section] = {}
        target = node
    target[last] = value


def _has(source: dict, dotted: str) -> bool:
    node: Any = source
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _expand_groups(layer: dict) -> dict:
    """``categories: {groups: {sanitari: false}, DIAGNOSI: true}`` -> explicit categories.

    In the same layer an explicit category wins over its group.
    """
    categories = layer.get("categories")
    if not isinstance(categories, dict) or "groups" not in categories:
        return layer
    groups = categories["groups"]
    if not isinstance(groups, dict):
        raise ValueError("categories.groups must be a mapping group: true/false")
    reg = registry()
    out = {k: v for k, v in categories.items() if k != "groups"}
    for group, enabled in groups.items():
        if str(group).lower() not in reg.groups:
            raise ValueError(
                f"Unknown group in categories.groups: {group} (allowed: {', '.join(reg.groups)})"
            )
        for category in reg.groups[str(group).lower()]:
            out.setdefault(category, bool(enabled))
    return {**layer, "categories": out}


@functools.cache
def _base() -> dict:
    cfg = read_yaml("default.yaml")
    cfg["prompts"] = {phase: read_yaml(f"prompts/{phase}.yaml") for phase in PHASES}
    return cfg


def base() -> dict:
    """The default configuration as a fresh dictionary (without the settings layer)."""
    return copy.deepcopy(_base())


def _user_file(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Pipeline configuration file not readable: {path}") from exc
    layer = parse_yaml(text, path)
    if "model" in layer:
        raise ValueError(
            f"{path}: the 'model' section is now 'generation'; the VLMs and their context are "
            "in the models file (ANONPIP_MODELS_FILE)"
        )
    if owned := [key for key in SETTINGS_KEYS if _has(layer, key)]:
        raise ValueError(
            f"{path}: keys owned by the ANONPIP_* settings: {', '.join(owned)} "
            "(set the variables instead)"
        )
    if owned := [key for key in MODELS_KEYS if _has(layer, key)]:
        raise ValueError(
            f"{path}: keys owned by the models file: {', '.join(owned)} "
            "(set them in ANONPIP_MODELS_FILE instead)"
        )
    return layer


def settings_layer(settings: Settings) -> dict:
    """The configuration keys that come from the service settings."""
    layer: dict = {}
    for dotted, attribute in SETTINGS_KEYS.items():
        _set(layer, dotted, getattr(settings, attribute))
    if not settings.vlm_api_key:
        _set(layer, "backend.api_key", None)
    return layer


def load_pipeline_config(
    settings: Settings, overrides: dict | None = None
) -> PipelineConfig:
    """Effective, validated configuration.

    Raises:
        ValueError: If a layer is invalid (pydantic's ``ValidationError`` is a ``ValueError``).
    """
    layers = [
        _user_file(settings.pipeline_config_file)
        if settings.pipeline_config_file
        else {},
        models_layer(settings.models_file),
        settings_layer(settings),
        overrides or {},
    ]
    cfg = base()
    for layer in layers:
        cfg = merge(cfg, _expand_groups(layer))
    return PipelineConfig.model_validate(cfg)


def request_overrides(
    mode: str | None = None,
    verification: str | None = None,
    models: dict[str, str] | None = None,
    enable: tuple[str, ...] = (),
    disable: tuple[str, ...] = (),
    extraction: str | None = None,
    transcript: bool | None = None,
    detectors: tuple[str, ...] | None = None,
) -> dict:
    """Overrides of a single request (API and web UI): only these keys.

    Args:
        mode: Redaction mode.
        verification: Verification mode.
        models: VLM of some phases (phase -> name of the models file catalog).
        enable: Groups or categories to switch on (comma separated values allowed).
        disable: Groups or categories to switch off.
        extraction: Engine for native PDF pages (docling | vlm).
        transcript: Also produce the transcript IN CLEAR TEXT (debugging).
        detectors: The complete set of active detectors and filters (``DETECTOR_KEYS``): the
            ones not listed are switched off. None: the configured ones.

    Raises:
        ValueError: On an unknown phase, detector, group or category.
    """
    out: dict = {}
    if transcript is not None:
        _set(out, "output.save_transcript", transcript)
    if extraction:
        _set(out, "phases.extraction.native_pdf.engine", extraction)
    if mode:
        _set(out, "phases.redaction.mode", mode)
    if verification:
        _set(out, "phases.verification.mode", verification)
    if detectors is not None:
        if unknown := set(detectors) - set(DETECTOR_KEYS):
            raise ValueError(f"unknown detectors: {', '.join(sorted(unknown))}")
        for name, dotted in DETECTOR_KEYS.items():
            _set(out, dotted, name in detectors)
    for phase, name in (models or {}).items():
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase}")
        _set(out, f"phases.{phase}.model", name)
    reg = registry()
    for entries, enabled in ((enable, True), (disable, False)):
        split = [v for entry in entries for v in entry.split(",") if v.strip()]
        for category in reg.expand([v.strip() for v in split]):
            _set(out, f"categories.{category}", enabled)
    return out


def effective(cfg: PipelineConfig) -> dict:
    """Configuration to show (API): without secrets."""
    data = cfg.model_dump()
    if data["backend"].get("api_key"):
        data["backend"]["api_key"] = "***"
    return data


def render(cfg: PipelineConfig, phase: str, key: str, **values: object) -> str:
    """Prompt ``phase.key`` with its placeholders filled (validated at load time)."""
    return string.Template(cfg.prompts[phase][key]).substitute(
        {k: str(v) for k, v in values.items()}
    )
