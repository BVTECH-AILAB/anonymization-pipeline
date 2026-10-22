"""Shared fixtures of the evaluation tests (synthetic data only)."""

from typing import Any

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from evals.config import EvalSettings


class _ExplicitSettings(EvalSettings):
    """Settings whose only source is the values passed to the constructor."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: Any) -> EvalSettings:
    """Settings built only from explicit values: neither ``.env`` nor the environment."""
    return _ExplicitSettings(**overrides)
