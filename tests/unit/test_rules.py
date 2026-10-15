import pytest

from anonmyzation_pipeline.services.anonymization.detect.rules import comuni, detect
from tests.conftest import FAKE_CF


def found(text: str) -> set[tuple[str, str]]:
    return {(s.label, s.text) for s in detect(text)}


def test_fiscal_code_is_found_even_with_a_wrong_check_character() -> None:
    # The textbook RSSMRA85M01H501U does not pass the checksum: still redacted, lower score.
    spans = [s for s in detect(f"C.F. {FAKE_CF}") if s.label == "FISCAL_CODE"]

    assert spans
    assert max(s.score for s in spans) == 0.9


def test_fiscal_code_with_ocr_noise_is_still_found() -> None:
    noisy = FAKE_CF[:6] + "8S" + FAKE_CF[8:]  # OCR read 5 as S

    assert ("FISCAL_CODE", noisy) in found(f"codice fiscale {noisy}")


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        (
            "Tessera sanitaria 80380000000000000001",
            "HEALTH_CARD",
            "80380000000000000001",
        ),
        ("Tel. 333 123 4567", "PHONE", "333 123 4567"),
        ("cell. +39 347 1752321", "PHONE", "+39 347 1752321"),
        ("fax 02 3408313", "PHONE", "02 3408313"),
        ("scrivere a mario.rossi85@example.it", "EMAIL", "mario.rossi85@example.it"),
        ("IBAN IT60X0542811101000000123456", "IBAN", "IT60X0542811101000000123456"),
        ("Nato il 01/08/1985 a Roma", "DATE_OF_BIRTH", "01/08/1985"),
        ("ricoverato il 03/02/2026", "DATE", "03/02/2026"),
        ("visita del 3 marzo 2026", "DATE", "3 marzo 2026"),
        ("di anni 64", "AGE", "64"),
        ("Residenza: Via Garibaldi 12, 00185 Roma", "ADDRESS", "Via Garibaldi 12"),
        ("Residenza: Via Garibaldi 12, 00185 Roma", "ADDRESS", "00185"),
        ("Residenza: Via Garibaldi 12, 00185 Roma", "LOCATION", "Roma"),
        ("CAP: 73100", "ADDRESS", "73100"),
        ("OMCeO MI n. 15959", "PROFESSIONAL_ID", "OMCeO MI n. 15959"),
        ("Cod. regionale 314474", "PROFESSIONAL_ID", "314474"),
        ("NRE 0300A123456789", "PRESCRIPTION_ID", "0300A123456789"),
        ("Nosologico 2026123456", "MEDICAL_RECORD", "2026123456"),
        ("documento n. CA12345AB", "ID_DOCUMENT", "CA12345AB"),
        (
            "referti: https://referti.example.it/r?id=2026123456",
            "URL",
            "https://referti.example.it/r?id=2026123456",
        ),
        ("nato a Bergamo il 25/09/1942", "LOCATION", "Bergamo"),
        ("residente in Firenze", "LOCATION", "Firenze"),
        ("Firenze, 24/08/2026", "LOCATION", "Firenze"),
        ("Piazza Gramsci, 129 – Bergamo", "LOCATION", "Bergamo"),
        ("CITTA': LECCE\nPROV: LE", "LOCATION", "LE"),
        ("Sig.ra Fuseli si presenta", "PERSON", "Fuseli"),
        ("Dott. Mario Rossi", "PERSON", "Mario Rossi"),
        ("Paziente: BIANCHI GIOVANNI", "PERSON", "BIANCHI GIOVANNI"),
        ("Stampato da Rosario Majorana il 23/09/2026", "PERSON", "Rosario Majorana"),
    ],
)
def test_italian_formats(text: str, label: str, value: str) -> None:
    assert (label, value) in found(text)


def test_dates_are_not_phones() -> None:
    assert not any(
        label == "PHONE" for label, _ in found("dal 21/01/2026 al 28/01/2026")
    )


def test_lab_values_are_not_pii() -> None:
    assert found("Creatinina 1,90 mg/dL (0,70 - 1,20); eGFR 34 mL/min") == set()


def test_plain_hospital_website_is_not_personal() -> None:
    assert not any(label == "URL" for label, _ in found("www.ospedale.it"))


def test_field_name_stops_at_the_next_label() -> None:
    assert ("PERSON", "BIANCHI GIOVANNI") in found("Paziente: BIANCHI GIOVANNI Sesso M")


def test_field_name_ignores_lowercase_words() -> None:
    values = {
        v
        for label, v in found("Il paziente Alessandro Mocenigo ha mostrato")
        if label == "PERSON"
    }

    assert "Alessandro Mocenigo ha" not in values


def test_municipalities_list_is_loaded() -> None:
    assert {"roma", "milano", "bergamo"} <= comuni()
