"""Rule-based detectors for Italian PII formats: validated codes, contacts,
dates, addresses and context-anchored identifiers. Tolerant to OCR noise
where the format allows it."""

import json
import re
from functools import lru_cache
from importlib import resources

from codicefiscale import codicefiscale

from anonymization_core.layers import Span

# --------------------------------------------------------------- patterns ---
_OMO = "0-9LMNPQRSTUV"  # omocodia digit substitutes
CF_STRICT = re.compile(
    rf"\b[A-Z]{{6}}[{_OMO}]{{2}}[ABCDEHLMPRST][{_OMO}]{{2}}[A-Z][{_OMO}]{{3}}[A-Z]\b"
)
CF_LOOSE = re.compile(
    r"\b[A-Z0-9]{6}[0-9OILSB]{2}[A-Z0-9][0-9OILSB]{2}[A-Z0-9][0-9OILSB]{3}[A-Z0-9]\b"
)
# any case, any letter/digit: repaired and accepted only with a valid check character
CF_ANY = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])")
# OCR confusions by position: digits where the codice fiscale has digits, letters elsewhere
_TO_DIGIT = str.maketrans("OQDILZSBGT", "0001125867")  # the value is upper-cased first
_TO_LETTER = str.maketrans("0125486", "OIZSBAG")
_CF_DIGITS = (6, 7, 9, 10, 12, 13, 14)
HEALTH_CARD = re.compile(r"(?<!\d)80380(?:\s?\d){15}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
URL = re.compile(r"\bhttps?://[^\s<>\"]+|\bwww\.[^\s<>\"]+")
# foreign IBAN (country code, check digits, up to 30 characters), checked with mod 97
IBAN_ANY = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"
)
# STP / ENI: health code of foreigners without a residence permit
STP_ENI = re.compile(r"\b(?:STP|ENI)[ ]?\d{3}[ ]?\d{3}[ ]?\d{7}\b")
# email read by OCR with spaces around "@" or the dot
EMAIL_SPACED = re.compile(r"\b[\w.+-]+[ ]?@[ ]?[\w-]+(?:[ ]?\.[ ]?[\w-]+)+\b")
IBAN = re.compile(r"\bIT\s?\d{2}\s?[A-Z]\s?(?:\d\s?){10}(?:[0-9A-Z]\s?){11}[0-9A-Z]\b")
MOBILE = re.compile(r"(?<![\w+])(?:\+39[ \t.]?)?3\d{2}(?:[ \t./-]?\d){6,7}(?!\d)")
LANDLINE = re.compile(r"(?<![\w+])(?:\+39[ \t.]?)?0\d{1,3}(?:[ \t./-]?\d){5,8}(?!\d)")
# foreign numbers ("+234 803 555 0164") and North American formats ("937.555.0178", "(752) 555-0206")
INTL_PHONE = re.compile(
    r"(?<![\w+])\+(?!39)[1-9]\d{0,2}[ \t.-]?(?:\(\d{1,4}\)[ \t.-]?)?\d(?:[ \t.-]?\d){6,12}(?!\d)"
)
US_PHONE = re.compile(r"(?<![\d(])(?:\(\d{3}\)\s?|\d{3}[.-])\d{3}[.-]\d{4}(?!\d)")
EXTENSION = re.compile(
    r"(?:\bat\s+|\bext\.?\s*|\binterno\s+|\bint\.\s*)(x?\d{3,5})\b", re.I
)
DATE_NUM = re.compile(
    r"(?<![\d/.-])(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}(?![\d/])"
)
MONTHS = "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
DATE_TXT = re.compile(
    rf"\b(?:0?[1-9]|[12]\d|3[01])\s+(?:{MONTHS})\s+(?:19|20)\d{{2}}\b", re.I
)
DOB_CTX = re.compile(
    r"(nat[oaie]\b|nascita|d\.\s?n\.|\bDN\b|data di nascita|\bDOB\b|birth|"
    # "ROSSI MARIO - n. 09.10.1961" (n. = nato), not "Prot. n.", "Rif. n."
    r"(?<!prot\.\s)(?<!rif\.\s)(?<!verbale\s)(?<!pratica\s)(?<!reg\.\s)\bn\.\s*$)",
    re.I,
)
# formats seen in English reports and DICOM headers, a date of birth only with its context
DATE_ISO = re.compile(
    r"(?<![\d-])(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?![\d-])"
)
DATE_COMPACT = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)"
)
DATE_MON = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])[- ](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|gen|mag|giu|lug|ago|set|ott|dic)"
    r"[a-z]*\.?[- ](?:19|20)?\d{2}\b",
    re.I,
)
AGE = re.compile(
    r"\b(?:di\s+anni|età:?|eta':?)\s*(\d{1,3})\b|\b(\d{1,3})\s*anni\b", re.I
)
STREET = re.compile(
    r"\b(?:Via|Viale|V\.le|Piazza|P\.zza|Piazzale|Corso|C\.so|Vicolo|Largo|Strada|Contrada|C\.da|Località|Loc\.|Borgo|Lungomare|Salita)"
    r"[ \t]+[A-Za-zÀ-ÿ0-9'.]+(?:[ \t]+[A-Za-zÀ-ÿ0-9'.]+){0,4}?,?[ \t]*(?:n\.?[ \t]*)?\d{1,4}(?:[ \t]?/[ \t]?[A-Za-z0-9]|[ \t]?bis|[ \t]?[A-Z]\b)?",
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
# "Ord. Med. RN n. 2654", "Albo PE n. 3391", "iscr. Ordine RN n.", "Ord. Naz. Biologi n. 1960"
PROF_REG = re.compile(
    r"\b(?:ord(?:ine)?\.?|albo|iscr(?:izione|\.)?|omceo|biologi)\b[^\n\d]{0,32}?\bn[.°]?\s*(\d{3,6})\b"
    r"|\bord(?:ine)?\.?\s+med(?:ici)?\.?\s+[A-Z]{2}\s+(\d{3,6})\b",
    re.I,
)
# DICOM header dump: "(0010,0010) PatientName ROSSI^MARIO"
DICOM = re.compile(r"\((?:0008|0010|0020|0032),[0-9A-F]{4}\)[ \t]+([^\n]+)")
_DICOM_WORD = re.compile(r"[A-Za-z]+")
DICOM_LABEL = {
    "patientname": "PERSON",
    "referringphysician": "PERSON",
    "requestingphysician": "PERSON",
    "operatorsname": "PERSON",
    "performingphysician": "PERSON",
    "patientid": "MEDICAL_RECORD",
    "otherpatientids": "MEDICAL_RECORD",
    "accessionnumber": "MEDICAL_RECORD",
    "studyinstanceuid": "MEDICAL_RECORD",
    "patientbirthdate": "DATE_OF_BIRTH",
    "patientaddress": "ADDRESS",
    "patienttelephone": "PHONE",
    "patienttelephonenumbers": "PHONE",
}
NRE = re.compile(r"(?<![\w])\d{3,5}A\d{9,10}(?![\w])")
CIE = re.compile(r"\b[A-Z]{2}\d{5}[A-Z]{2}\b")
# keyword -> following identifier. The identifier must contain at least 4 digits.
ID_CTX = re.compile(
    r"\b(nosologico|n\.?\s?nosologico|cartella(?:\s+clinica)?|accesso|campione|richiesta|accession(?:\s*(?:number|no\.?|#))?|"
    r"referto|protocollo|prot\.|episodio|pratica|ID\s+stampa|ID\s+paziente|cod(?:ice|\.)?\s+paziente|MRN|"
    r"prenotazione(?:\s+CUP)?|pren\.|CUP|accettazione|acc\.|istologico|ist\.|verbale|ricovero|rif\.|"
    r"specimen(?:\s+ID)?|sample(?:\s+ID)?|medicaid(?:\s+ID)?|member\s+ID|group\s+(?:no\.?|#)|hospital\s+no\.?|"
    r"impegnativa|NRE|ricetta|documento|carta\s+d'identit[aà]|C\.?\s?I\.?|passaporto|passport|patente|"
    r"permesso(?:\s+di)?\s+sogg(?:iorno|\.))(?![\w-])"
    # an optional qualifier: "Cartella amb.:", "Richiesta lab. n.", "Accesso PS:"
    r"(?:[ \t]+(?:[a-zà-ù]{2,12}\.|(?-i:[A-Z]{2,4}))(?=[\s:]))?"
    r"[\s:.#]*(?:n[.°]|n(?=\s)|nr\.?|numero|no\.|no(?=\s))?\s*[:.]?\s*([A-Z0-9][A-Z0-9./-]{3,24}[A-Z0-9])",
    re.I,
)
ID_KIND = {
    "documento": "ID_DOCUMENT",
    "carta": "ID_DOCUMENT",
    "ci": "ID_DOCUMENT",
    "c.i.": "ID_DOCUMENT",
    "passaporto": "ID_DOCUMENT",
    "patente": "ID_DOCUMENT",
    "passport": "ID_DOCUMENT",
    "permesso": "ID_DOCUMENT",
    "nre": "PRESCRIPTION_ID",
    "ricetta": "PRESCRIPTION_ID",
    "impegnativa": "PRESCRIPTION_ID",
}
PLACE_CTX = re.compile(
    r"(?i:\b(?:nat[oaie]\s+(?:a|ad|in):?|residente\s+(?:a|ad|in)|domiciliat[oaie]\s+(?:a|ad|in)|residenza:?|comune\s+di|"
    r"citta'?:?|città:?|luogo\s+di\s+nascita:?|prov(?:incia)?\.?:?))\s+"
    # the keywords in any case, the place capitalised ("residente a casa" is not a place)
    r"([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+(?:[ \t]+[A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+){0,2})",
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


# a name word: "Rossi" or "ROSSI"; up to three words in any mix ("Mario ROSSI")
_NAME_WORD = r"[A-ZÀ-Ý](?:[a-zà-ÿ'’]+|[A-ZÀ-Ý'’]+)"
_NAME = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{0,2}}"
# "Sig.ra Fuseli", "Dott. Mario Rossi", "Prof.ssa ROSSI"
HONORIFIC_NAME = re.compile(
    rf"\b(?:Sig\.(?:ra|na)?|Signor[ae]?|Dott\.(?:ssa)?|Dr\.(?:ssa)?|Prof\.(?:ssa)?|Ing\.|Avv\.)[ \t]+({_NAME})"
)
# form fields: "Paziente: BIANCHI GIOVANNI", "Cognome e nome\nRossi Mario"
FIELD_NAME = re.compile(
    rf"(?i:\b(?:Paziente|Assistit[oa]|Nominativo|Cognome\s+e\s+nome|COGNOME\s+E\s+NOME(?:\s+(?:del|della)\s+(?:paziente|assistit[oa]))?|Accompagnat[oa]\s+da|"
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
    "cognome",
    "nome",
    "indirizzo",
    "ruolo",
    "qualifica",
    "contatto",
    "familiari",
    "rapporto",
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


def _dicom_field(line: str) -> tuple[str, int, str] | None:
    """Label, value offset and value of a DICOM header line after its tag.

    The keyword may be split into words ("Patient Name", from a scanner's OCR
    layer or a text dump): the longest run of up to four words, separated only by
    spaces, that joins into a known keyword wins; the rest of the line is the value.
    """
    words = list(_DICOM_WORD.finditer(line[:80]))
    if not words or line[: words[0].start()].strip():
        return None
    for k in range(min(4, len(words)), 0, -1):
        gaps = [line[words[j].end() : words[j + 1].start()] for j in range(k - 1)]
        if any(g.strip(" \t") for g in gaps):
            continue
        key = "".join(w.group() for w in words[:k]).lower()
        rest = line[words[k - 1].end() :]
        if key in DICOM_LABEL and rest.strip():
            value = rest.strip()
            return DICOM_LABEL[key], words[k - 1].end() + rest.index(value), value
    return None


def repair_cf(value: str) -> str | None:
    """The codice fiscale in ``value`` (any case, OCR digit/letter confusions), or None.

    A repaired value is accepted only if its check character is valid, so a
    lot number or a word of 16 characters is never taken for a code.
    """
    chars = list(value.upper())
    for i, c in enumerate(chars):
        chars[i] = (
            c.translate(_TO_DIGIT) if i in _CF_DIGITS else c.translate(_TO_LETTER)
        )
    code = "".join(chars)
    return code if _cf_ok(code) else None


def is_code(value: str) -> bool:
    """True for a whole personal code: codice fiscale (check character valid,
    OCR confusions repaired), tessera sanitaria, STP or ENI.

    Args:
        value: The candidate, spaces removed.
    """
    return bool(
        repair_cf(value) or HEALTH_CARD.fullmatch(value) or STP_ENI.fullmatch(value)
    )


def iban_ok(value: str) -> bool:
    """ISO 13616 check (mod 97) of an IBAN, spaces ignored."""
    s = value.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def _span(
    m: re.Match, label: str, score: float, group: int = 0, src: str = "rules"
) -> Span:
    return Span(m.start(group), m.end(group), label, score, src, m.group(group))


def detect(text: str) -> list[Span]:
    """Spans of every format rule on ``text``: codes, contacts, dates, addresses, places, names in typical phrasings."""
    out: list[Span] = []
    dates = [(m.start(), m.end()) for m in DATE_NUM.finditer(text)]

    for m in CF_STRICT.finditer(text):
        out.append(_span(m, "FISCAL_CODE", 1.0 if _cf_ok(m.group()) else 0.9))
    for m in CF_LOOSE.finditer(text):
        s = m.group()
        if sum(c.isalpha() for c in s) >= 6 and sum(c.isdigit() for c in s) >= 4:
            out.append(_span(m, "FISCAL_CODE", 0.7))
    for m in HEALTH_CARD.finditer(text):
        out.append(_span(m, "HEALTH_CARD", 1.0))
    for m in CF_ANY.finditer(text):
        value = m.group()
        if CF_STRICT.fullmatch(value):
            continue  # already found above
        if CF_STRICT.fullmatch(value.upper()):  # written in lower or mixed case
            out.append(_span(m, "FISCAL_CODE", 1.0 if _cf_ok(value.upper()) else 0.9))
        elif repair_cf(
            value
        ):  # OCR confusions, accepted only with a valid check character
            out.append(_span(m, "FISCAL_CODE", 0.95))
    for m in STP_ENI.finditer(text):
        out.append(_span(m, "FISCAL_CODE", 1.0))
    for m in EMAIL.finditer(text):
        out.append(_span(m, "EMAIL", 1.0))
    for m in EMAIL_SPACED.finditer(text):
        if " " in m.group():
            out.append(_span(m, "EMAIL", 0.9))
    for m in URL.finditer(text):
        u = m.group()
        if "?" in u or sum(c.isdigit() for c in u) >= 6 or CF_LOOSE.search(u.upper()):
            out.append(_span(m, "URL", 0.95))
    for m in IBAN.finditer(text):
        out.append(_span(m, "IBAN", 1.0))
    for m in IBAN_ANY.finditer(text):
        if iban_ok(m.group()):
            out.append(_span(m, "IBAN", 1.0))
    for rx in (MOBILE, LANDLINE, INTL_PHONE, US_PHONE):
        for m in rx.finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in dates):
                continue
            digits = sum(c.isdigit() for c in m.group())
            if 8 <= digits <= 15:
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
    for m in EXTENSION.finditer(text):
        ctx = text[max(0, m.start() - 40) : m.start()].lower()
        if re.search(r"phone|tel|call|chiam|recapit", ctx):
            out.append(_span(m, "PHONE", 0.85, 1))
    for rx in (DATE_ISO, DATE_MON):
        for m in rx.finditer(text):
            ctx = text[max(0, m.start() - 30) : m.start()]
            out.append(
                _span(m, "DATE_OF_BIRTH" if DOB_CTX.search(ctx) else "DATE", 0.9)
            )
    for m in DATE_COMPACT.finditer(text):
        if DOB_CTX.search(text[max(0, m.start() - 30) : m.start()]):
            out.append(_span(m, "DATE_OF_BIRTH", 0.9))
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
    for m in PROF_REG.finditer(text):
        g = 1 if m.group(1) else 2
        out.append(_span(m, "PROFESSIONAL_ID", 0.95, g))
    for m in DICOM.finditer(text):
        if found := _dicom_field(m.group(1)):
            label, offset, value = found
            if value.lower() not in ("(vuoto)", "(empty)", "none"):
                start = m.start(1) + offset
                out.append(Span(start, start + len(value), label, 0.95, "rules", value))
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
