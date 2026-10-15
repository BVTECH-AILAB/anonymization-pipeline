"""Rule-based detectors for Italian PII formats: validated codes, contacts,
dates, addresses and context-anchored identifiers. Tolerant to OCR noise
where the format allows it."""

import json
import re
from functools import lru_cache
from importlib import resources

from codicefiscale import codicefiscale

from anonmyzation_pipeline.services.anonymization.layers import Span

# --------------------------------------------------------------- patterns ---
_OMO = "0-9LMNPQRSTUV"  # omocodia digit substitutes
CF_STRICT = re.compile(
    rf"\b[A-Z]{{6}}[{_OMO}]{{2}}[ABCDEHLMPRST][{_OMO}]{{2}}[A-Z][{_OMO}]{{3}}[A-Z]\b"
)
CF_LOOSE = re.compile(
    r"\b[A-Z0-9]{6}[0-9OILSB]{2}[A-Z0-9][0-9OILSB]{2}[A-Z0-9][0-9OILSB]{3}[A-Z0-9]\b"
)
HEALTH_CARD = re.compile(r"(?<!\d)80380(?:\s?\d){15}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
URL = re.compile(r"\bhttps?://[^\s<>\"]+|\bwww\.[^\s<>\"]+")
IBAN = re.compile(r"\bIT\s?\d{2}\s?[A-Z]\s?(?:\d\s?){10}(?:[0-9A-Z]\s?){12}\b")
MOBILE = re.compile(r"(?<![\w+])(?:\+39[\s.]?)?3\d{2}(?:[\s./-]?\d){6,7}(?!\d)")
LANDLINE = re.compile(r"(?<![\w+])(?:\+39[\s.]?)?0\d{1,3}(?:[\s./-]?\d){5,8}(?!\d)")
DATE_NUM = re.compile(
    r"(?<![\d/.-])(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}(?![\d/])"
)
MONTHS = "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
DATE_TXT = re.compile(
    rf"\b(?:0?[1-9]|[12]\d|3[01])\s+(?:{MONTHS})\s+(?:19|20)\d{{2}}\b", re.I
)
DOB_CTX = re.compile(r"(nat[oaie]\b|nascita|d\.\s?n\.|\bDN\b|data di nascita)", re.I)
AGE = re.compile(
    r"\b(?:di\s+anni|età:?|eta':?)\s*(\d{1,3})\b|\b(\d{1,3})\s*anni\b", re.I
)
STREET = re.compile(
    r"\b(?:Via|Viale|V\.le|Piazza|P\.zza|Piazzale|Corso|C\.so|Vicolo|Largo|Strada|Contrada|C\.da|Località|Loc\.|Borgo|Lungomare|Salita)"
    r"\s+[A-Za-zÀ-ÿ0-9'.]+(?:\s+[A-Za-zÀ-ÿ0-9'.]+){0,4}?,?\s*(?:n\.?\s*)?\d{1,4}(?:\s?/\s?[A-Za-z0-9]|\s?bis|\s?[A-Z]\b)?",
    re.I,
)
CAP_CITY = re.compile(
    r"(?<![\d.,])\b(\d{5})[ \t]*\n?[ \t]*([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+(?:[ \t][A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+)?)"
)
CAP_CTX = re.compile(r"\bCAP:?\s*(\d{5})\b", re.I)
PROV = re.compile(r"(?<=[A-Za-zà-ú])\s\(([A-Z]{2})\)")
PROF_ID = re.compile(
    r"\bOMCeO\s+[A-Z]{2}\s+n\.?\s*\d{3,6}\b|\b(?:cod(?:ice)?\.?\s+regionale|iscr(?:izione)?\.?\s+albo(?:\s+n\.)?)\s*:?\s*([A-Z0-9]{4,10})\b",
    re.I,
)
NRE = re.compile(r"(?<![\w])\d{3,5}A\d{9,10}(?![\w])")
CIE = re.compile(r"\b[A-Z]{2}\d{5}[A-Z]{2}\b")
# keyword -> following identifier. The identifier must contain at least 4 digits.
ID_CTX = re.compile(
    r"\b(nosologico|n\.?\s?nosologico|cartella(?:\s+clinica)?|accesso|campione|richiesta|accession|referto|protocollo|"
    r"episodio|pratica|ID\s+stampa|ID\s+paziente|MRN|prenotazione|impegnativa|NRE|ricetta|"
    r"documento|carta\s+d'identit[aà]|C\.?\s?I\.?|passaporto|patente)\b"
    r"[\s:.]*(?:n\.?|n°|nr\.?|numero)?\s*[:.]?\s*([A-Z0-9][A-Z0-9/-]{4,24})",
    re.I,
)
ID_KIND = {
    "documento": "ID_DOCUMENT",
    "carta": "ID_DOCUMENT",
    "ci": "ID_DOCUMENT",
    "c.i.": "ID_DOCUMENT",
    "passaporto": "ID_DOCUMENT",
    "patente": "ID_DOCUMENT",
    "nre": "PRESCRIPTION_ID",
    "ricetta": "PRESCRIPTION_ID",
    "impegnativa": "PRESCRIPTION_ID",
}
PLACE_CTX = re.compile(
    r"\b(?:nat[oaie]\s+(?:a|ad|in)|residente\s+(?:a|ad|in)|domiciliat[oaie]\s+(?:a|ad|in)|residenza:?|comune\s+di|"
    r"citta'?:?|città:?|luogo\s+di\s+nascita:?|prov(?:incia)?\.?:?)\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+(?:[ \t]+[A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+){0,2})",
    re.I,
)
# "... il 12/03/1958 a Milano", "residente in Firenze": preposition + capitalised municipality
PLACE_PREP = re.compile(
    r"(?<![\w'])(?:a|ad|in|di|da)\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]{2,}(?:\s[A-ZÀ-Ý][a-zà-ÿ'-]+)?)"
)
# "Via Roma 14 – Bergamo", "Via Roma 14, Milano"
PLACE_AFTER_ADDR = re.compile(
    r"\d{1,4}(?:\s?/\s?[A-Za-z0-9]|\s?bis)?\s*[-–,]\s*([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]{2,}(?:\s[A-ZÀ-Ý][a-zà-ÿ'-]+)?)"
)
PLACE_STOP = {
    "medicina",
    "terapia",
    "ospedale",
    "reparto",
    "pronto",
    "salute",
    "regione",
    "nefrologia",
    "dicembre",
    "ore",
    "sua",
    "via",
    "piazza",
    "corso",
    "data",
    "fede",
    "vita",
    "calcio",
    "paese",
    "fondo",
    "lago",
    "monte",
    "porto",
}
PLACE_DATE = re.compile(
    r"(?m)^([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+(?:\s[A-ZÀ-Ý][a-zà-ÿ'-]+)?),\s+(?:li\s+)?\d{1,2}[/.]\d{1,2}[/.]\d{4}"
)


_NAME = r"[A-ZÀ-Ý][a-zà-ÿ'’]+(?:[ \t]+[A-ZÀ-Ý][a-zà-ÿ'’]+){0,2}|[A-ZÀ-Ý]{2,}(?:[ \t]+[A-ZÀ-Ý]{2,}){0,2}"
# "Sig.ra Fuseli", "Dott. Mario Rossi", "Prof.ssa ROSSI"
HONORIFIC_NAME = re.compile(
    rf"\b(?:Sig\.(?:ra|na)?|Signor[ae]?|Dott\.(?:ssa)?|Dr\.(?:ssa)?|Prof\.(?:ssa)?|Ing\.|Avv\.)[ \t]+({_NAME})"
)
# form fields: "Paziente: BIANCHI GIOVANNI", "Cognome e nome\nRossi Mario"
FIELD_NAME = re.compile(
    rf"(?i:\b(?:Paziente|Assistit[oa]|Nominativo|Cognome\s+e\s+nome|COGNOME\s+E\s+NOME[^:\n]*|Accompagnat[oa]\s+da|"
    rf"Medico\s+curante|Medico\s+richiedente|Prescrittore|Stampato\s+da|ritirato\s+da|eseguito\s+dal\s+TSRM|"
    rf"firmato\s+digitalmente\s+da))"
    rf":?[ \t]*\n?[ \t]*({_NAME})"
)
NAME_STOP = {
    "sesso",
    "nato",
    "nata",
    "codice",
    "data",
    "età",
    "eta",
    "anni",
    "ore",
    "documento",
    "campione",
    "telefono",
    "residenza",
    "il",
    "la",
    "del",
    "della",
    "reparto",
    "medico",
    "dott",
    "dott.ssa",
    "prof",
    "sig",
    "pag",
}


@lru_cache
def comuni() -> frozenset[str]:
    """Active Italian municipalities, lower-case, from the codicefiscale package data."""
    data = json.loads(
        resources.files("codicefiscale")
        .joinpath("data/municipalities.json")
        .read_text()
    )
    return frozenset(m["name"].lower() for m in data if m.get("active"))


def _cf_ok(s: str) -> bool:
    try:
        return codicefiscale.is_valid(s)
    except Exception:
        return False


def _span(
    m: re.Match, label: str, score: float, group: int = 0, src: str = "rules"
) -> Span:
    return Span(m.start(group), m.end(group), label, score, src, m.group(group))


def detect(text: str) -> list[Span]:
    out: list[Span] = []
    up = text  # CF/IDs are matched case-sensitively on the original text
    dates = [(m.start(), m.end()) for m in DATE_NUM.finditer(text)]

    for m in CF_STRICT.finditer(up):
        out.append(_span(m, "FISCAL_CODE", 1.0 if _cf_ok(m.group()) else 0.9))
    for m in CF_LOOSE.finditer(up):
        s = m.group()
        if sum(c.isalpha() for c in s) >= 6 and sum(c.isdigit() for c in s) >= 4:
            out.append(_span(m, "FISCAL_CODE", 0.7))
    for m in HEALTH_CARD.finditer(text):
        out.append(_span(m, "HEALTH_CARD", 1.0))
    for m in EMAIL.finditer(text):
        out.append(_span(m, "EMAIL", 1.0))
    for m in URL.finditer(text):
        u = m.group()
        if "?" in u or sum(c.isdigit() for c in u) >= 6 or CF_LOOSE.search(u.upper()):
            out.append(_span(m, "URL", 0.95))
    for m in IBAN.finditer(text):
        out.append(_span(m, "IBAN", 1.0))
    for rx in (MOBILE, LANDLINE):
        for m in rx.finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in dates):
                continue
            digits = sum(c.isdigit() for c in m.group())
            if 8 <= digits <= 13:
                ctx = text[max(0, m.start() - 25) : m.start()].lower()
                out.append(
                    _span(
                        m,
                        "PHONE",
                        1.0
                        if re.search(r"tel|cell|fax|recapit|telefono|numero", ctx)
                        else 0.85,
                    )
                )
    for rx in (DATE_NUM, DATE_TXT):
        for m in rx.finditer(text):
            ctx = text[max(0, m.start() - 30) : m.start()]
            out.append(
                _span(m, "DATE_OF_BIRTH" if DOB_CTX.search(ctx) else "DATE", 0.95)
            )
    for m in AGE.finditer(text):
        g = 1 if m.group(1) else 2
        out.append(_span(m, "AGE", 0.9, g))
    for m in STREET.finditer(text):
        out.append(_span(m, "ADDRESS", 0.95))
    for m in CAP_CITY.finditer(text):
        city = m.group(2)
        if city.lower() not in comuni() and city.split()[0].lower() in comuni():
            city = city.split()[0]
        if city.lower() in comuni():
            out.append(_span(m, "ADDRESS", 0.9, 1))
            out.append(
                Span(m.start(2), m.start(2) + len(city), "LOCATION", 0.9, "rules", city)
            )
    for m in CAP_CTX.finditer(text):
        out.append(_span(m, "ADDRESS", 0.9, 1))
    for m in PROF_ID.finditer(text):
        out.append(_span(m, "PROFESSIONAL_ID", 1.0, 1 if m.group(1) else 0))
    for m in NRE.finditer(text):
        out.append(_span(m, "PRESCRIPTION_ID", 0.9))
    for m in CIE.finditer(text):
        out.append(_span(m, "ID_DOCUMENT", 0.8))
    for m in ID_CTX.finditer(text):
        val = m.group(2)
        if sum(c.isdigit() for c in val) < 4 or DATE_NUM.fullmatch(val):
            continue
        key = re.sub(r"\s+", " ", m.group(1).lower()).split()[0]
        out.append(_span(m, ID_KIND.get(key, "MEDICAL_RECORD"), 0.95, 2))
    for rx, score in ((HONORIFIC_NAME, 0.9), (FIELD_NAME, 0.85)):
        for m in rx.finditer(text):
            words = m.group(1).split()
            # cut at the first word that is a field label, not a name
            keep = []
            for w in words:
                if w.lower().strip(".:") in NAME_STOP:
                    break
                keep.append(w)
            if keep and not (len(keep) == 1 and len(keep[0]) < 3):
                val = " ".join(keep)
                start = m.start(1)
                out.append(Span(start, start + len(val), "PERSON", score, "rules", val))
    cset = comuni()
    for m in PLACE_CTX.finditer(text):
        val = m.group(1)
        # trim trailing words that are not part of the municipality name
        words = val.split()
        for k in range(len(words), 0, -1):
            cand = " ".join(words[:k])
            if cand.lower() in cset or k == 1:
                start = m.start(1)
                out.append(
                    Span(
                        start,
                        start + len(cand),
                        "LOCATION",
                        0.95 if cand.lower() in cset else 0.7,
                        "rules",
                        cand,
                    )
                )
                break
    for m in PLACE_AFTER_ADDR.finditer(text):
        name = m.group(1)
        for cand in (name, name.split()[0]):
            if cand.lower() in cset:
                out.append(
                    Span(
                        m.start(1),
                        m.start(1) + len(cand),
                        "LOCATION",
                        0.85,
                        "rules",
                        cand,
                    )
                )
                break
    for m in PLACE_PREP.finditer(text):
        name = m.group(1)
        for cand in (name, name.split()[0]):
            if cand.lower() in cset and cand.lower() not in PLACE_STOP:
                out.append(
                    Span(
                        m.start(1),
                        m.start(1) + len(cand),
                        "LOCATION",
                        0.85,
                        "rules",
                        cand,
                    )
                )
                break
    for m in PLACE_DATE.finditer(text):
        if m.group(1).lower() in cset:
            out.append(_span(m, "LOCATION", 0.9, 1))
    for m in PROV.finditer(text):
        before = text[max(0, m.start() - 30) : m.start()].split()
        if before and before[-1].lower() in cset:
            out.append(_span(m, "LOCATION", 0.85, 1))
    return out
