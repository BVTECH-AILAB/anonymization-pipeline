"""Things that look like PII but are not: eponyms, drugs, saints in facility names,
clinical vocabulary. Decisions are contextual: "riflesso di Babinski" is kept,
"Sig. Corti" or "Concetta Corti" is a person."""

import re
import unicodedata

EPONYMS = {
    "addison",
    "alzheimer",
    "apgar",
    "babinski",
    "barré",
    "basedow",
    "bell",
    "blumberg",
    "brudzinski",
    "child",
    "corti",
    "crohn",
    "cushing",
    "down",
    "fazekas",
    "fleischner",
    "forlanini",
    "giordano",
    "glasgow",
    "golgi",
    "graves",
    "guillain",
    "hashimoto",
    "hodgkin",
    "his",
    "kaposi",
    "karnofsky",
    "kerley",
    "kernig",
    "killip",
    "lasègue",
    "lasegue",
    "malpighi",
    "mallampati",
    "morgagni",
    "murphy",
    "nyha",
    "pacini",
    "parkinson",
    "pugh",
    "rankin",
    "raynaud",
    "riva-rocci",
    "romberg",
    "ruffini",
    "santorini",
    "sjögren",
    "valsalva",
    "wells",
    "willis",
    "wilson",
    "wirsung",
    "gold",
    "kdigo",
    "ckd-epi",
    "fanconi",
    "korsakoff",
    "wernicke",
    "broca",
    "tourette",
    "huntington",
    "menière",
    "paget",
    "pott",
    "reiter",
    "still",
    "takayasu",
    "horton",
    "dupuytren",
}

# words before an eponym: "segno di Murphy", "morbo di Crohn", "organo del Corti"
_TRIGGER = re.compile(
    r"(?:morbo|malattia|sindrome|segn[oi]|manovra|riflesso|scala|criteri[oa]?|classificazione|metodo|organo|dott[oi]|"
    r"ernia|corpuscol[oi]|apparato|tiroidite|poligono|test|punteggio|score|line[ae]|angolo|ansa|legamento|tumore|"
    r"linfoma|frattura|triade|cellule|fascio|paralisi|demenza|anemia|cisti|diverticolo|sec\.|secondo)"
    r"\s+(?:di|del|dello|della|dei|degli|delle|de|d')?\s*$",
    re.I,
)
# words after an eponym: "Glasgow Coma Scale", "Fazekas 1", "Child-Pugh B"
_SUFFIX = re.compile(r"^\s*(?:coma|scale|score|-pugh|test|\d)", re.I)
_SAINT = re.compile(
    r"(?:\b(?:san|santa|sant'|santo|santi|santissima|ss\.|s\.)\s*)$", re.I
)
_HONORIFIC = re.compile(
    r"(?:\b(?:sig\.?(?:ra|na)?|signor[ae]?|dott\.?(?:ssa)?|prof\.?(?:ssa)?|dr\.?|ing\.?|avv\.?)\s*)$",
    re.I,
)

DRUGS = {
    "metformina",
    "ramipril",
    "bisoprololo",
    "atorvastatina",
    "furosemide",
    "warfarin",
    "apixaban",
    "pantoprazolo",
    "salbutamolo",
    "tiotropio",
    "levotiroxina",
    "prednisone",
    "metilprednisolone",
    "levodopa",
    "carbidopa",
    "insulina",
    "glargine",
    "lispro",
    "allopurinolo",
    "amlodipina",
    "dapagliflozin",
    "sacubitril",
    "valsartan",
    "mesalazina",
    "adalimumab",
    "ustekinumab",
    "alteplase",
    "fondaparinux",
    "nitroglicerina",
    "ketoprofene",
    "ibuprofene",
    "colecalciferolo",
    "salmeterolo",
    "fluticasone",
    "piperacillina",
    "tazobactam",
    "iopamidolo",
    "gadobutrolo",
    "acido acetilsalicilico",
    "eutirox",
    "coumadin",
    "tachipirina",
    "lasix",
    "eliquis",
    "xarelto",
    "rivaroxaban",
}

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
    s = unicodedata.normalize("NFKC", s).strip(" .,;:()[]'\"").lower()
    return re.sub(r"\s+", " ", s)


def is_eponym_use(text: str, start: int, end: int) -> bool:
    """True if text[start:end] is an eponym used as such (not a person)."""
    word = norm(text[start:end])
    before = text[max(0, start - 40) : start]
    if _HONORIFIC.search(before):
        return False
    if _TRIGGER.search(before):
        return True
    # "Fazekas 1", "Glasgow Coma Scale": the suffix only counts for known eponyms
    return word in EPONYMS and len(word.split()) == 1


def is_saint(text: str, start: int) -> bool:
    return bool(_SAINT.search(text[max(0, start - 12) : start]))


def is_not_pii(text: str, start: int, end: int, label: str) -> bool:
    """Filter for NER-style spans (PERSON, LOCATION, ORGANIZATION)."""
    val = norm(text[start:end])
    if not val or val in STOP or val in DRUGS:
        return True
    if all(w in STOP or w in DRUGS for w in val.split()):
        return True
    if label == "PERSON":
        if is_saint(text, start):
            return True
        if is_eponym_use(text, start, end):
            return True
        if not any(c.isalpha() for c in val) or len(val) < 2:
            return True
    return False
