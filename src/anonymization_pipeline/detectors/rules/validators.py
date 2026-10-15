"""Checksum validators for Italian and international identifiers (from the anonimizzatore project)."""

import re

_CF_ODD = {
    **{
        str(d): v
        for d, v in zip(range(10), [1, 0, 5, 7, 9, 13, 15, 17, 19, 21], strict=True)
    },
    **dict(
        zip(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            [
                1,
                0,
                5,
                7,
                9,
                13,
                15,
                17,
                19,
                21,
                2,
                4,
                18,
                20,
                11,
                3,
                6,
                8,
                12,
                14,
                16,
                10,
                22,
                25,
                24,
                23,
            ],
            strict=True,
        )
    ),
}
_CF_EVEN = {
    **{str(d): d for d in range(10)},
    **{c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
}

CF_RE = re.compile(
    r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$"
)


def cf_check_char(cf15: str) -> str:
    total = 0
    for i, c in enumerate(cf15):
        total += _CF_ODD[c] if i % 2 == 0 else _CF_EVEN[c]
    return chr(ord("A") + total % 26)


def valid_cf(cf: str) -> bool:
    """Codice fiscale with a valid check character (omocodia allowed)."""
    cf = cf.upper().replace(" ", "")
    if not CF_RE.match(cf):
        return False
    return cf_check_char(cf[:15]) == cf[15]


def valid_vat(value: str) -> bool:
    """Italian VAT number (partita IVA)."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or digits == "0" * 11:
        return False
    total = 0
    for i, c in enumerate(digits[:10]):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(digits[10])


def valid_iban(iban: str) -> bool:
    iban = re.sub(r"\s", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    if iban.startswith("IT") and len(iban) != 27:
        return False
    rearranged = iban[4:] + iban[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


def luhn(number: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    if not digits:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def valid_card(number: str) -> bool:
    digits = re.sub(r"\D", "", number)
    return 13 <= len(digits) <= 19 and digits[0] in "3456" and luhn(digits)


def valid_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and all(
        p.isdigit() and 0 <= int(p) <= 255 and str(int(p)) == p for p in parts
    )


_VIN_VALUES = {
    **{str(d): d for d in range(10)},
    **dict(
        zip(
            "ABCDEFGHJKLMNPRSTUVWXYZ",
            [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9],
            strict=True,
        )
    ),
}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def valid_vin(vin: str) -> bool:
    """Chassis number (ISO 3779) with the check digit in ninth position."""
    vin = vin.upper()
    if (
        not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin)
        or not re.search(r"\d", vin)
        or not re.search(r"[A-Z]", vin)
    ):
        return False
    remainder = (
        sum(_VIN_VALUES[c] * w for c, w in zip(vin, _VIN_WEIGHTS, strict=True)) % 11
    )
    return vin[8] == ("X" if remainder == 10 else str(remainder))
