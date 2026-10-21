"""Things that look like PII but are not: eponyms, drugs, saints in facility names,
clinical vocabulary. Decisions are contextual: "riflesso di Babinski" is kept,
"Sig. Corti" or "Concetta Corti" is a person."""

import re
import unicodedata

from anonymization_core.detect import eponyms, labels

# words before an eponym: "segno di Murphy", "morbo di Crohn", "organo del Corti"
# a stray symbol, or a word made only of digits: "emb T $ Esa", "paz NGUY Q 3". A digit
# inside a word is an OCR misreading of a letter ("Cerul1o"): still a name.
_NOT_A_NAME_CHAR = re.compile(r"[^\w\s'’.,^()\-]|(?<![\w])\d+(?![\w])")
_SAINT = re.compile(
    r"(?:\b(?:san|santa|sant'|santo|santi|santissima|ss\.|s\.)\s*)$", re.I
)
_HONORIFIC = re.compile(
    r"(?:\b(?:sig\.?(?:ra|na)?|signor[ae]?|dott\.?(?:ssa)?|prof\.?(?:ssa)?|dr\.?|ing\.?|avv\.?)\s*)$",
    re.I,
)

# drug names, active ingredients and brands: never a person (eponyms.yaml)
DRUGS = eponyms.lists().drugs

# clinical / document vocabulary that NER sometimes tags as person or place
STOP = {
    "paziente",
    "gentile",
    "collega",
    "medico",
    "direttore",
    "reparto",
    "nefrologia",
    "cardiologia",
    "neurologia",
    "radiologia",
    "pneumologia",
    "gastroenterologia",
    "medicina",
    "interna",
    "generale",
    "pronto",
    "soccorso",
    "terapia",
    "diagnosi",
    "anamnesi",
    "esame",
    "referto",
    "laboratorio",
    "ospedale",
    "policlinico",
    "presidio",
    "azienda",
    "ospedaliera",
    "regionale",
    "sistema",
    "socio",
    "sanitario",
    "nazionale",
    "asst",
    "u.o.c.",
    "u.o.",
    "studio",
    "tel",
    "fax",
    "pec",
    "pag",
    "nota",
    "note",
    "firma",
    "timbro",
    "data",
    "ore",
    "codice",
    "fiscale",
    "tessera",
    "sanitaria",
    "residenza",
    "telefono",
    "nosologico",
    "campione",
    "accesso",
    "documento",
    "triage",
    "esito",
    "ricovero",
    "dimissione",
    "sig",
    "sig.ra",
    "dott",
    "dott.ssa",
    "prof",
    "prof.ssa",
    "egfr",
    "pcr",
    "ecg",
    "tc",
    "rm",
    "rx",
    "inr",
    "tsh",
    "bpco",
    "nyha",
    "gold",
}


def norm(s: str) -> str:
    """Lower case, NFKC, single spaces, without surrounding punctuation."""
    s = unicodedata.normalize("NFKC", s).strip(" .,;:()[]'\"").lower()
    return re.sub(r"\s+", " ", s)


def is_eponym_use(text: str, start: int, end: int) -> bool:
    """True if text[start:end] is an eponym used as such (not a person).

    Only words of the eponym lists qualify, and only in a clinical context
    (``eponyms.is_excluded``): a surname after "test" or "metodo" stays a person.
    """
    if _HONORIFIC.search(text[max(0, start - 40) : start]):
        return False
    return bool(norm(text[start:end])) and eponyms.is_excluded(text, start, end)


def is_saint(text: str, start: int) -> bool:
    """True if the word at ``start`` follows San / Santa / S. (a facility or street, not a person)."""
    return bool(_SAINT.search(text[max(0, start - 12) : start]))


def is_not_pii(text: str, start: int, end: int, label: str) -> bool:
    """Filter for NER-style spans (PERSON, LOCATION, ORGANIZATION, ADDRESS)."""
    raw = text[start:end]
    val = norm(raw)
    if label == "PERSON" and _HONORIFIC.search(text[max(0, start - 40) : start]):
        return False  # "Sig.ra Gentile", "Dott. Bruno": a title makes it a person
    if not val or val in STOP or val in DRUGS:
        return True
    if all(w in STOP or w in DRUGS for w in val.split()):
        return True
    if label in ("PERSON", "LOCATION"):
        # a name or a place has no digits nor stray symbols: "paz NGUY Q 3" and
        # "emb T $ Esa" are OCR fragments ("^" separates DICOM name parts)
        if _NOT_A_NAME_CHAR.search(raw):
            return True
        if labels.rules().any_label.fullmatch(raw.strip(" .:")):
            return True  # a field label: "Telefono", "Nome"
        if is_eponym_use(text, start, end):
            return True  # "Glasgow 15", "unità Hounsfield"
    if label == "PERSON":
        if is_saint(text, start):
            return True
        if not any(c.isalpha() for c in val) or len(val) < 2:
            return True
    return False
