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


def test_prod_forbids_the_transcript() -> None:
    assert (
        make_settings(environment="prod", allow_transcript=True).allow_transcript
        is False
    )
    assert (
        make_settings(environment="staging", allow_transcript=True).allow_transcript
        is True
    )


def test_credentials_have_no_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("ANONPIP_VLM_API_KEY", "ANONPIP_VAULT_KEY"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.vlm_api_key == ""
    assert settings.vault_key == ""


def test_make_settings_ignores_environment_and_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANONPIP_MAX_JOBS=9\n")
    monkeypatch.setenv("ANONPIP_ALLOW_TRANSCRIPT", "true")

    settings = make_settings()

    assert settings.allow_transcript is False
    assert settings.max_jobs == Settings.model_fields["max_jobs"].default
