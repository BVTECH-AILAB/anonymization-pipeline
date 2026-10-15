"""Layered loading: default.yaml -> user file (service configuration, read once at startup),
then the choices of a single request on top of it."""

import copy
from collections.abc import Iterable
from pathlib import Path

from anonymization_pipeline.config import Settings
from anonymization_pipeline.pipeline_config.categories import registry
from anonymization_pipeline.pipeline_config.resources import parse_yaml, read_yaml
from anonymization_pipeline.pipeline_config.schema import PipelineConfig


class ProtectedCategoryError(ValueError):
    """A request tried to switch off a protected category (Italian message for users)."""


def merge(base: dict, above: dict) -> dict:
    """Deep merge: ``above`` wins, nested mappings are merged key by key."""
    out = copy.deepcopy(base)
    for key, value in (above or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


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


def base() -> dict:
    """default.yaml over every category of the registry switched on, as a fresh dictionary."""
    cfg = _expand_groups(read_yaml("default.yaml"))
    cfg["categories"] = {
        **dict.fromkeys(registry().descriptions, True),
        **(cfg.get("categories") or {}),
    }
    return cfg


def _user_file(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Pipeline configuration file not readable: {path}") from exc
    return parse_yaml(text, path)


def load_pipeline_config(settings: Settings) -> PipelineConfig:
    """Service configuration: defaults and the user file, validated.

    Called once at startup: an invalid file stops the service instead of failing requests.

    Raises:
        ValueError: If a layer is invalid (pydantic's ``ValidationError`` is a ``ValueError``).
    """
    cfg = base()
    if settings.pipeline_config_file:
        cfg = merge(cfg, _expand_groups(_user_file(settings.pipeline_config_file)))
    return PipelineConfig.model_validate(cfg)


def _split(entries: Iterable[str]) -> list[str]:
    return [v.strip() for entry in entries for v in entry.split(",") if v.strip()]


def request_config(
    service: PipelineConfig,
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
) -> PipelineConfig:
    """Configuration of a single request: the service one with categories switched on or off.

    A request can switch on any category and switch off only those outside the protection floor;
    ``disable`` wins over ``enable`` for the same category.

    Args:
        service: Service configuration (``load_pipeline_config``).
        enable: Groups or categories to switch on (comma separated values allowed).
        disable: Groups or categories to switch off.

    Raises:
        ProtectedCategoryError: A protected category is in ``disable``.
        ValueError: On an unknown group or category (the message reaches API users).
    """
    reg = registry()
    on = reg.expand(_split(enable))
    off = reg.expand(_split(disable))
    if blocked := [c for c in off if c in set(service.protected())]:
        raise ProtectedCategoryError(
            f"Categorie protette, non disattivabili: {', '.join(blocked)}"
        )
    categories = {
        **service.categories,
        **dict.fromkeys(on, True),
        **dict.fromkeys(off, False),
    }
    return PipelineConfig.model_validate(
        {**service.model_dump(), "categories": categories}
    )
