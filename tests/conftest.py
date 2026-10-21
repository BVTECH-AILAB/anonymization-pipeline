"""Fixtures of the tools/ tests.

The tools still read the service ``Settings``; this module goes away when
they get their own configuration.
"""

from typing import Any

from anonmyzation_pipeline.config import Settings
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class _ExplicitSettings(Settings):
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


def make_settings(**overrides: Any) -> Settings:
    """Settings built only from explicit values: neither ``.env`` nor the environment."""
    values: dict[str, Any] = {
        "environment": "local",
        "log_level": "DEBUG",
        "preload_models": False,
    }
    return _ExplicitSettings(**(values | overrides))
