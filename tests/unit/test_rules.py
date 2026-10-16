import pytest
from codicefiscale import codicefiscale

from anonmyzation_pipeline.services.anonymization.detect.rules import (
    comuni,
    detect,
    repair_cf,
)
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
        ("cell. +39 347 0000001", "PHONE", "+39 347 0000001"),
        ("fax 02 0000001", "PHONE", "02 0000001"),
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


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        ("N. accettazione: VS-26-004817", "MEDICAL_RECORD", "VS-26-004817"),
        ("Acc. RX26-0412234 del 22/09/2026", "MEDICAL_RECORD", "RX26-0412234"),
        ("Prenotazione CUP: CUP-BG-26-3378145", "MEDICAL_RECORD", "CUP-BG-26-3378145"),
        ("Verbale n. COL-27/2026", "MEDICAL_RECORD", "COL-27/2026"),
        ("N. istologico: B25.06614", "MEDICAL_RECORD", "B25.06614"),
        ("Accession # NGD-26-0918-44731", "MEDICAL_RECORD", "NGD-26-0918-44731"),
        ("Medicaid ID OH4471029384", "MEDICAL_RECORD", "OH4471029384"),
        ("Permesso di soggiorno: I12007845", "ID_DOCUMENT", "I12007845"),
        ("Ord. Med. RN n. 2654", "PROFESSIONAL_ID", "2654"),
        ("iscr. Albo PE n. 3391", "PROFESSIONAL_ID", "3391"),
        ("Ord. Naz. Biologi n. 1960", "PROFESSIONAL_ID", "1960"),
        ("Ord. Med. RN 2654", "PROFESSIONAL_ID", "2654"),
        ("Recapito: +234 803 555 0164", "PHONE", "+234 803 555 0164"),
        ("Patient phone 937.555.0178", "PHONE", "937.555.0178"),
        ("Phone (752) 555-0206", "PHONE", "(752) 555-0206"),
        ("discussed by phone with Dr. Reyes at x4471", "PHONE", "x4471"),
        ("DOB: 1979-03-22", "DATE_OF_BIRTH", "1979-03-22"),
        ("DOB 30-Nov-1966", "DATE_OF_BIRTH", "30-Nov-1966"),
        ("ROSSI MARIO - n. 09.10.1961", "DATE_OF_BIRTH", "09.10.1961"),
        ("(0010,0030) PatientBirthDate 19910214", "DATE_OF_BIRTH", "19910214"),
        ("(0010,0010) PatientName ROSSI^MARIO", "PERSON", "ROSSI^MARIO"),
        ("(0010,0020) PatientID 77812345", "MEDICAL_RECORD", "77812345"),
        # keywords split into words by a scanner's OCR layer
        ("(0010,0010) Patient Name ROSSI^MARIO", "PERSON", "ROSSI^MARIO"),
        (
            "(0020,000D) Study Instance UID 1.2.826.0.1.3680043.2",
            "MEDICAL_RECORD",
            "1.2.826.0.1.3680043.2",
        ),
        ("(0010,0010) PatientName Mario Rossi", "PERSON", "Mario Rossi"),
        ("Nato a: Bergamo", "LOCATION", "Bergamo"),
        ("Cartella amb.: CARD-PM-2023-1187", "MEDICAL_RECORD", "CARD-PM-2023-1187"),
        ("Richiesta lab. n. LAB-26-55120", "MEDICAL_RECORD", "LAB-26-55120"),
        ("Accesso PS: 2026004417", "MEDICAL_RECORD", "2026004417"),
    ],
)
def test_identifiers_after_labels_and_foreign_formats(
    text: str, label: str, value: str
) -> None:
    assert (label, value) in found(text)


def test_label_keywords_need_a_real_identifier() -> None:
    labels = {
        lab
        for lab, _ in found(
            "Rif. normale, verbale della riunione, acc. ok, referto del 12/03/2026"
        )
    }

    assert "MEDICAL_RECORD" not in labels


def test_compact_date_without_birth_context_is_not_a_birth_date() -> None:
    assert not any(lab == "DATE_OF_BIRTH" for lab, _ in found("lotto 20240315 scad."))


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        (f"il paziente {FAKE_CF.lower()} si presenta", "FISCAL_CODE", FAKE_CF.lower()),
        ("Paziente STP 190 001 0001234", "FISCAL_CODE", "STP 190 001 0001234"),
        ("IBAN: DE89370400440532013000", "IBAN", "DE89370400440532013000"),
        ("IBAN DE89 3704 0044 0532 0130 00", "IBAN", "DE89 3704 0044 0532 0130 00"),
        ("scrivere a mario.rossi @ example.it", "EMAIL", "mario.rossi @ example.it"),
    ],
)
def test_formats_ported_from_the_reconstructed_architecture(
    text: str, label: str, value: str
) -> None:
    assert (label, value) in found(text)


def test_codice_fiscale_repair_needs_a_valid_check_character() -> None:
    # the textbook code with its valid check character, then read by OCR with O for 0
    valid = codicefiscale.encode(
        lastname="Rossi",
        firstname="Mario",
        gender="M",
        birthdate="01/08/1985",
        birthplace="Roma",
    )
    misread = valid[:13] + "O" + valid[14:]  # the 14th character is the digit 0

    assert repair_cf(misread) == valid
    assert ("FISCAL_CODE", misread) in found(f"C.F. {misread}")
    assert repair_cf("ABCDEFGHIJKLMNOP") is None  # 16 letters, not a code
    assert repair_cf("LOTTO2024ABC1234") is None
