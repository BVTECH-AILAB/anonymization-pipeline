"""Synthetic identifiers with valid checksums: codice fiscale, IBAN, partita IVA, health card.

Every value is fictitious. Random values are deterministic: the same seed gives the same value,
so the dataset is rebuilt identically.
"""

import datetime
import random
import unicodedata

# Belfiore code of the birth places the generation prompt may use (Italian municipalities and,
# for people born abroad, Z codes of the country).
BIRTH_PLACES: dict[str, str] = {
    "Roma": "H501",
    "Milano": "F205",
    "Napoli": "F839",
    "Torino": "L219",
    "Palermo": "G273",
    "Genova": "D969",
    "Bologna": "A944",
    "Firenze": "D612",
    "Bari": "A662",
    "Catania": "C351",
    "Venezia": "L736",
    "Verona": "L781",
    "Messina": "F158",
    "Padova": "G224",
    "Trieste": "L424",
    "Brescia": "B157",
    "Parma": "G337",
    "Taranto": "L049",
    "Modena": "F257",
    "Perugia": "G478",
    "Livorno": "E625",
    "Ravenna": "H199",
    "Cagliari": "B354",
    "Foggia": "D643",
    "Rimini": "H294",
    "Salerno": "H703",
    "Ferrara": "D548",
    "Sassari": "I452",
    "Latina": "E472",
    "Pescara": "G482",
    "Bergamo": "A794",
    "Vicenza": "L840",
    "Trento": "L378",
    "Bolzano": "A952",
    "Ancona": "A271",
    "Lecce": "E506",
    "Udine": "L483",
    "Pisa": "G702",
    "Potenza": "G942",
    "Campobasso": "B519",
    "Aosta": "A326",
    "Siena": "I726",
    "Romania": "Z129",
    "Marocco": "Z330",
    "Albania": "Z100",
    "Cina": "Z210",
    "Ucraina": "Z138",
    "Germania": "Z112",
    "Francia": "Z110",
    "Senegal": "Z343",
    "India": "Z222",
    "Filippine": "Z216",
    "Egitto": "Z336",
    "Brasile": "Z602",
    "Tunisia": "Z352",
    "Nigeria": "Z335",
    "Pakistan": "Z236",
}

_MONTHS = "ABCDEHLMPRST"
_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [1, 0, 5, 7, 9, 13, 15, 17, 19, 21]
        + [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16]
        + [10, 22, 25, 24, 23],
        strict=True,
    )
)
_EVEN = {c: (int(c) if c.isdigit() else ord(c) - ord("A")) for c in _ODD}
_VOWELS = set("AEIOU")
# first digits of the TEAM card number after 80 (health) and 380 (Italy): regional codes
_TEAM_REGIONS = ("010", "030", "050", "080", "090", "120", "150", "160", "190", "200")


def _letters(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c for c in plain.upper() if c.isalpha())


def _code(letters: str, is_name: bool) -> str:
    consonants = [c for c in letters if c not in _VOWELS]
    vowels = [c for c in letters if c in _VOWELS]
    if is_name and len(consonants) >= 4:
        return consonants[0] + consonants[2] + consonants[3]
    return "".join(consonants + vowels + ["X", "X", "X"])[:3]


def control_char(chars: str) -> str:
    """Control character shared by the codice fiscale and the CIN of the IBAN."""
    total = sum((_ODD if i % 2 == 0 else _EVEN)[c] for i, c in enumerate(chars.upper()))
    return chr(ord("A") + total % 26)


def codice_fiscale(
    first_name: str, last_name: str, sex: str, birth_date: str, birth_place: str
) -> str:
    """Codice fiscale computed from the personal data.

    Args:
        first_name: First name(s).
        last_name: Surname (apostrophes, spaces and accents are ignored).
        sex: ``M`` or ``F``.
        birth_date: ISO date ``YYYY-MM-DD``.
        birth_place: A key of ``BIRTH_PLACES``.

    Raises:
        ValueError: On an unknown birth place, an invalid sex or date.
    """
    if birth_place not in BIRTH_PLACES:
        raise ValueError(f"unknown birth place: {birth_place}")
    if sex not in ("M", "F"):
        raise ValueError(f"invalid sex: {sex}")
    born = datetime.date.fromisoformat(birth_date)
    day = born.day + (40 if sex == "F" else 0)
    body = (
        _code(_letters(last_name), is_name=False)
        + _code(_letters(first_name), is_name=True)
        + f"{born.year % 100:02d}"
        + _MONTHS[born.month - 1]
        + f"{day:02d}"
        + BIRTH_PLACES[birth_place]
    )
    return body + control_char(body)


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(n))


def iban(
    abi: str | None = None,
    cab: str | None = None,
    account: str | None = None,
    seed: str = "",
) -> str:
    """Italian IBAN with valid CIN and check digits; missing parts are random from ``seed``."""
    rng = random.Random(seed)
    abi = abi or _digits(rng, 5)
    cab = cab or _digits(rng, 5)
    account = account or _digits(rng, 12)
    bban = control_char(abi + cab + account) + abi + cab + account
    numeric = "".join(str(int(c, 36)) for c in bban + "IT00")
    return f"IT{98 - int(numeric) % 97:02d}{bban}"


def partita_iva(base: str | None = None, seed: str = "") -> str:
    """Partita IVA: ten digits (random from ``seed`` when missing) plus the check digit."""
    base = base or _digits(random.Random(seed), 10)
    total = 0
    for i, c in enumerate(base):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return base + str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    """True when the digit string passes the Luhn check."""
    total = 0
    for i, c in enumerate(reversed(number)):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def tessera_sanitaria(seed: str = "") -> str:
    """20-digit TEAM health card number (80 + 380 + region + progressive + Luhn digit)."""
    rng = random.Random(seed)
    body = "80380" + rng.choice(_TEAM_REGIONS) + _digits(rng, 11)
    return next(body + d for d in "0123456789" if luhn_valid(body + d))
