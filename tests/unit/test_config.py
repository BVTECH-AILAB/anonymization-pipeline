from pathlib import Path

import pytest

from agenas_service.config import Settings
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://user@host:5432/db", "postgresql+asyncpg://user@host:5432/db"),
        ("postgres://user@host:5432/db", "postgresql+asyncpg://user@host:5432/db"),
        (
            "postgresql+asyncpg://user@host:5432/db",
            "postgresql+asyncpg://user@host:5432/db",
        ),
    ],
)
def test_database_url_accepts_cnpg_uri(raw: str, expected: str) -> None:
    assert make_settings(database_url=raw).database_url == expected


def test_database_url_has_no_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TEMPLATE_DATABASE_URL", raising=False)

    assert Settings().database_url == ""


def test_default_environment_is_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("TEMPLATE_ENVIRONMENT", raising=False)

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
