import logging

import pytest

from anonymization_pipeline.detectors.rules.validators import valid_cf
from anonymization_pipeline.privacy import (
    REDACTED,
    PiiRedactingFilter,
    hash_identifier,
    mask_identifier,
    redact_cf,
    redact_job_ids,
    redact_user_text,
)
from tests.conftest import FAKE_CF, make_settings


def test_hash_identifier_is_normalised_sha256() -> None:
    expected = "4f5241d8029d3b1f6eb70f3ef3f67bfbba75793830a803f0dd62258ec008c3de"

    assert hash_identifier(FAKE_CF) == expected
    assert hash_identifier(f"  {FAKE_CF.lower()} ") == expected


def test_mask_identifier() -> None:
    assert mask_identifier(FAKE_CF) == "R***Q"
    assert mask_identifier(FAKE_CF, prefix=2, suffix=0) == "RS***"
    assert mask_identifier("") == ""


@pytest.mark.parametrize(
    "text",
    [
        f"utente {FAKE_CF} non trovato",
        f"file {FAKE_CF}_lista.csv",
        f"omocodia {FAKE_CF[:6]}LT{FAKE_CF[8:]}",
        f"minuscolo {FAKE_CF.lower()}",
    ],
)
def test_redact_cf_masks_codice_fiscale(text: str) -> None:
    redacted = redact_cf(text)

    assert "***" in redacted
    assert FAKE_CF[1:-1].lower() not in redacted.lower()


def test_redact_cf_leaves_hashes_alone() -> None:
    digest = hash_identifier(FAKE_CF)

    assert redact_cf(digest) == digest


def test_redact_user_text_is_redacted_by_default() -> None:
    assert redact_user_text("ho mal di testa", make_settings()) == REDACTED


def test_redact_user_text_in_staging_still_masks_cf() -> None:
    settings = make_settings(environment="staging", extended_log_context=True)

    assert redact_user_text(f"paziente {FAKE_CF}", settings) == "paziente R***Q"


def test_filter_redacts_args_and_exceptions() -> None:
    try:
        raise ValueError(f"bad value {FAKE_CF}")
    except ValueError as exc:
        record = logging.LogRecord(
            "x",
            logging.ERROR,
            __file__,
            1,
            "cf=%s data=%s",
            (FAKE_CF, {"cf": [FAKE_CF]}),
            None,
        )
        record.exc_info = (type(exc), exc, exc.__traceback__)

    PiiRedactingFilter().filter(record)

    assert FAKE_CF not in record.getMessage()
    assert record.exc_text is not None
    assert FAKE_CF not in record.exc_text


def test_fake_cf_has_a_valid_check_character() -> None:
    # the rules detector validates the checksum: an invalid test value is never detected by it
    assert valid_cf(FAKE_CF)


def test_job_ids_in_paths_are_shortened() -> None:
    job_id = "l4kmKuceRjeYqRyME69-kQ"

    text = redact_job_ids(f'"GET /anonymizations/{job_id}/download HTTP/1.1" 200')

    assert job_id not in text
    assert "/anonymizations/l4kmKuce***/download" in text
    assert redact_job_ids("/anonymizations/short") == "/anonymizations/short"


def test_filter_shortens_job_ids_in_access_logs() -> None:
    job_id = "l4kmKuceRjeYqRyME69-kQ"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", f"/anonymizations/{job_id}", "1.1", 200),
        None,
    )

    PiiRedactingFilter().filter(record)

    assert job_id not in record.getMessage()


def test_filter_masks_arguments_that_are_not_strings() -> None:
    class Url:
        def __str__(self) -> str:
            return f"http://x/anonymizations/l4kmKuceRjeYqRyME69-kQ?cf={FAKE_CF}"

    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "GET %s", (Url(),), None
    )

    PiiRedactingFilter().filter(record)

    assert FAKE_CF not in record.getMessage()
    assert "l4kmKuceRjeYqRyME69-kQ" not in record.getMessage()
