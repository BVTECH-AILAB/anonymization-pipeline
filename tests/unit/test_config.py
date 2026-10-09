from pathlib import Path

import pytest

from anonymization_pipeline.config import Settings
from tests.conftest import make_settings


def test_default_environment_is_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("ANONPIP_ENVIRONMENT", raising=False)

    assert Settings().environment == "prod"


def test_prod_forbids_user_text_and_debug() -> None:
    settings = make_settings(
        environment="prod", log_level="DEBUG", extended_log_context=True
    )

    assert settings.log_level == "INFO"
    assert settings.extended_log_context is False
    assert settings.log_user_text is False
    assert settings.docs_enabled is False


def test_staging_may_enable_user_text() -> None:
    settings = make_settings(environment="staging", extended_log_context=True)

    assert settings.log_user_text is True


def test_invalid_log_level_falls_back_to_info() -> None:
    assert make_settings(log_level="verbose").log_level == "INFO"
