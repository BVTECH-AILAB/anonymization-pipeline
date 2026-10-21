"""PII taxonomy shared by the synthetic generator, detectors, redaction and UI.

Each label has:
- ``kind``: "text" (found in text) or "visual" (found in pixels / images)
- ``redact``: default policy (True = always redacted; False = quasi-identifier,
  redaction configurable, not redacted by default)
- ``it``: name shown in the UI chips
- ``tag``: label written in the redaction box in the PDF
- ``short``: fallback tag when the box is too small for ``tag``
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    code: str
    kind: str
    redact: bool
    it: str
    tag: str
    short: str


LABELS: dict[str, Label] = {
    lab.code: lab
    for lab in [
        # --- identity -------------------------------------------------------
        Label("PERSON", "text", True, "Nomi", "NOME", "N"),
        Label("FISCAL_CODE", "text", True, "Codice fiscale", "CODICE FISCALE", "CF"),
        Label(
            "HEALTH_CARD", "text", True, "Tessera sanitaria", "TESSERA SANITARIA", "TS"
        ),
        Label("ID_DOCUMENT", "text", True, "Documento d'identità", "DOCUMENTO", "DOC"),
        Label(
            "DATE_OF_BIRTH", "text", True, "Data di nascita", "DATA DI NASCITA", "DN"
        ),
        Label("AGE", "text", False, "Età", "ETÀ", "ETÀ"),
        # --- location & contacts ---------------------------------------------
        Label("ADDRESS", "text", True, "Indirizzi", "INDIRIZZO", "IND"),
        Label("LOCATION", "text", True, "Luoghi", "LUOGO", "LUO"),
        Label("PHONE", "text", True, "Telefono/fax", "TELEFONO", "TEL"),
        Label("EMAIL", "text", True, "Email/PEC", "EMAIL", "@"),
        # --- healthcare identifiers --------------------------------------------
        Label("MEDICAL_RECORD", "text", True, "ID sanitari", "ID SANITARIO", "ID"),
        Label("PRESCRIPTION_ID", "text", True, "Ricetta (NRE)", "NRE", "NRE"),
        Label(
            "PROFESSIONAL_ID",
            "text",
            True,
            "ID professionali",
            "ID PROFESSIONALE",
            "IDP",
        ),
        # --- financial / technical -------------------------------------------
        Label("IBAN", "text", True, "IBAN", "IBAN", "IBAN"),
        Label("URL", "text", True, "URL personali", "URL", "URL"),
        # --- quasi-identifiers (not redacted by default) ---------------------------
        Label("ORGANIZATION", "text", False, "Strutture", "STRUTTURA", "STR"),
        Label("DATE", "text", False, "Date cliniche", "DATA", "D"),
        # --- visual ------------------------------------------------------------
        Label("SIGNATURE", "visual", True, "Firme", "FIRMA", "F"),
        Label("STAMP", "visual", True, "Timbri", "TIMBRO", "T"),
        Label("BARCODE", "visual", True, "Codici a barre", "CODICE A BARRE", "BC"),
        Label("QRCODE", "visual", True, "QR / DataMatrix", "QR CODE", "QR"),
        Label("FACE", "visual", True, "Volti/foto", "FOTO", "FOTO"),
        Label("HANDWRITING", "visual", True, "Note manoscritte", "MANOSCRITTO", "MS"),
    ]
}

# identifiers that are never quasi-identifiers: every redacted value of these labels
# is searched across the whole document and must not survive anywhere in the output
STRONG_LABELS = frozenset(
    {
        "URL",
        "FISCAL_CODE",
        "HEALTH_CARD",
        "PHONE",
        "EMAIL",
        "MEDICAL_RECORD",
        "ID_DOCUMENT",
        "PRESCRIPTION_ID",
        "PROFESSIONAL_ID",
        "IBAN",
        "DATE_OF_BIRTH",
    }
)
