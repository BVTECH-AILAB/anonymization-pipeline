from pathlib import Path

import pytest

from src.config import Settings
from tests.conftest import make_settings


def test_safe_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    for var in (
        "ANONPIP_REDACTION_STYLE",
        "ANONPIP_PRELOAD_MODELS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings()

    assert settings.redaction_style == "black"
    assert settings.preload_models is True
    assert settings.redaction_policy == {
        "DATE": False,
        "AGE": False,
        "ORGANIZATION": False,
    }


def test_quasi_identifiers_can_be_redacted() -> None:
    settings = make_settings(
        redact_clinical_dates=True, redact_age=True, redact_organizations=True
    )

    assert set(settings.redaction_policy.values()) == {True}


@pytest.mark.parametrize(
    "field", ["max_upload_mb", "max_pages", "job_ttl_minutes", "ocr_dpi"]
)
def test_limits_are_validated(field: str) -> None:
    with pytest.raises(ValueError):
        make_settings(**{field: 0})


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
