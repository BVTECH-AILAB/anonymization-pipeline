"""Evaluation settings and logging: the engine config, without the service."""

import io
import logging

import pytest

from anonymization_eval.config import EvalSettings
from anonymization_eval.log import setup_logging
from tests.conftest import make_settings


def test_pipeline_config_carries_the_engine_settings() -> None:
    config = make_settings(
        ocr_server_url="http://ocr.test",
        ner_url="http://ner.test",
        ner_retries=4,
        redact_age=True,
    ).pipeline_config()

    assert config.ocr_server_url == "http://ocr.test"
    assert config.ner_url == "http://ner.test"
    assert config.ner_retries == 4
    assert config.redaction_policy == {
        "DATE": False,
        "AGE": True,
        "ORGANIZATION": False,
    }


def test_settings_read_the_service_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONPIP_OCR_SERVER_URL", "http://ocr.env:1")
    monkeypatch.setenv("ANONPIP_REDACTION_STYLE", "label")
    monkeypatch.setenv("ANONPIP_MAX_JOBS", "3")  # a service setting: ignored

    settings = EvalSettings(_env_file=None)

    assert settings.ocr_server_url == "http://ocr.env:1"
    assert settings.redaction_style == "label"


def test_setup_logging_is_idempotent() -> None:
    root = logging.getLogger()
    first, second = io.StringIO(), io.StringIO()

    setup_logging("INFO", stream=first)
    setup_logging("INFO", stream=second)
    logging.getLogger("anonymization_eval").info("evaluated %d documents", 3)

    ours = [
        h for h in root.handlers if getattr(h, "_anonymization_eval_handler", False)
    ]
    assert len(ours) == 1
    assert first.getvalue() == ""
    assert "INFO anonymization_eval: evaluated 3 documents" in second.getvalue()
    root.removeHandler(ours[0])
