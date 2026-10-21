"""The service around the engine: error mapping and engine configuration."""

import pytest
from anonymization_core import errors
from anonymization_core.config import PipelineConfig

from anonmyzation_pipeline import errors as service_errors
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("engine_error", "code", "status"),
    [
        (errors.NerUnavailableError, "NER_UNAVAILABLE", 503),
        (errors.UnsupportedFormatError, "UNSUPPORTED_FORMAT", 415),
        (errors.UnreadableDocumentError, "UNREADABLE_DOCUMENT", 422),
        (errors.PasswordProtectedError, "PASSWORD_PROTECTED", 422),
        (errors.TooManyPagesError, "TOO_MANY_PAGES", 413),
        (errors.PageNotFoundError, "JOB_NOT_FOUND", 404),
        (errors.AnonymizationError, "INTERNAL_ERROR", 500),
    ],
)
def test_service_error_maps_every_engine_error(
    engine_error: type[errors.AnonymizationError], code: str, status: int
) -> None:
    mapped = service_errors.service_error(engine_error())

    assert (mapped.code, mapped.status_code) == (code, status)


def test_settings_build_the_engine_config() -> None:
    config = make_settings(
        ocr_server_url="http://ocr.test",
        ner_url="http://ner.test",
        ner_retries=4,
        redact_age=True,
    ).pipeline_config()

    assert isinstance(config, PipelineConfig)
    assert config.ocr_server_url == "http://ocr.test"
    assert config.ner_url == "http://ner.test"
    assert config.ner_retries == 4
    assert config.redaction_policy["AGE"] is True
