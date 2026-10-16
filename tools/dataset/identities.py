"""Fake Italian identities for the synthetic corpus.

Names come from Faker it_IT. Detection must NOT use Faker's name lists as
dictionaries, or recall on this corpus would be inflated.
"""

import random
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from codicefiscale import codicefiscale
from faker import Faker

# (city, province, CAP, phone prefix)
CITIES = [
    ("Milano", "MI", "20121", "02"),
    ("Roma", "RM", "00185", "06"),
    ("Napoli", "NA", "80133", "081"),
    ("Torino", "TO", "10121", "011"),
    ("Palermo", "PA", "90133", "091"),
    ("Bari", "BA", "70121", "080"),
    ("Bologna", "BO", "40121", "051"),
    ("Firenze", "FI", "50122", "055"),
    ("Genova", "GE", "16121", "010"),
    ("Verona", "VR", "37121", "045"),
    ("Padova", "PD", "35121", "049"),
    ("Catania", "CT", "95121", "095"),
    ("Lecce", "LE", "73100", "0832"),
    ("Pavia", "PV", "27100", "0382"),
    ("Bergamo", "BG", "24121", "035"),
    ("Brescia", "BS", "25121", "030"),
    ("Monza", "MB", "20900", "039"),
    ("Cagliari", "CA", "09124", "070"),
    ("Ancona", "AN", "60121", "071"),
    ("Perugia", "PG", "06121", "075"),
    ("Trento", "TN", "38122", "0461"),
    ("Udine", "UD", "33100", "0432"),
    ("Salerno", "SA", "84121", "089"),
    ("Modena", "MO", "41121", "059"),
    ("Parma", "PR", "43121", "0521"),
    ("Pescara", "PE", "65121", "085"),
    ("Sassari", "SS", "07100", "079"),
    ("Varese", "VA", "21100", "0332"),
    ("Como", "CO", "22100", "031"),
    ("Cremona", "CR", "26100", "0372"),
]

STREET_PREFIX = [
    "Via",
    "Via",
    "Via",
    "Viale",
    "Piazza",
    "Corso",
    "Vicolo",
    "Largo",
    "Strada",
]
STREET_NAMES = [
    "Garibaldi",
    "Roma",
    "Mazzini",
    "Cavour",
    "Giuseppe Verdi",
    "Dante Alighieri",
    "XX Settembre",
    "Vittorio Emanuele II",
    "IV Novembre",
    "della Repubblica",
    "dei Mille",
    "San Francesco",
    "Gramsci",
    "Matteotti",
    "Leopardi",
    "Manzoni",
    "delle Rose",
    "dei Tigli",
    "Marconi",
    "Fratelli Bandiera",
    "Santa Caterina",
    "Aldo Moro",
    "della Libertà",
    "Torino",
    "Nazionale",
]

# Surnames that collide with medical eponyms: a patient may be called like this
# while the text also mentions the eponym, which must NOT be redacted.
EPONYM_SURNAMES = {
    "Pacini": "corpuscoli di Pacini",
    "Morgagni": "ernia di Morgagni",
    "Santorini": "dotto di Santorini",
    "Corti": "organo del Corti",
    "Golgi": "apparato di Golgi",
    "Malpighi": "corpuscoli del Malpighi",
    "Forlanini": "metodo di Forlanini",
    "Ruffini": "corpuscoli di Ruffini",
}

MAIL_DOMAINS = [
    "gmail.com",
    "libero.it",
    "virgilio.it",
    "hotmail.it",
    "yahoo.it",
    "alice.it",
    "tiscali.it",
]


def ascii_lower(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


@dataclass
class Person:
    first: str
    last: str
    sex: str  # "M" | "F"
    dob: date
    birthplace: str
    birth_prov: str
    cf: str
    street: str
    civic: str
    cap: str
    city: str
    prov: str
    phone: str
    email: str
    health_card: str
    id_document: str

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def full_rev_upper(self) -> str:
        return f"{self.last} {self.first}".upper()

    @property
    def initial_form(self) -> str:
        return f"{self.last} {self.first[0]}."

    @property
    def address(self) -> str:
        return f"{self.street}, {self.civic}"

    @property
    def title(self) -> str:
        return "Sig." if self.sex == "M" else "Sig.ra"

    def age(self, at: date) -> int:
        return (
            at.year
            - self.dob.year
            - ((at.month, at.day) < (self.dob.month, self.dob.day))
        )

    def g(self, m: str, f: str) -> str:
        """Gendered word: g('nato', 'nata')."""
        return m if self.sex == "M" else f


@dataclass
class Doctor:
    first: str
    last: str
    sex: str
    title: str  # Dott. / Dott.ssa / Prof. / Prof.ssa
    specialty: str
    omceo: str  # registration, e.g. "OMCeO MI n. 41237"
    cf: str
    phone: str
    email: str

    @property
    def full(self) -> str:
        return f"{self.title} {self.first} {self.last}"

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Hospital:
    name: str
    asst: str
    city: str
    prov: str
    address: str
    cap: str
    phone: str
    fax: str
    web: str
    color: tuple[float, float, float]
    accent: tuple[float, float, float]


HOSPITAL_NAMES = [
    "Ospedale Maggiore della Carità",
    "Presidio Ospedaliero Santa Maria degli Angeli",
    "Ospedale San Giovanni Battista",
    "Policlinico Sant'Orsola",
    "Ospedale Civile Santissima Annunziata",
    "Azienda Ospedaliera San Camillo",
    "Ospedale Sacro Cuore",
    "Presidio Ospedaliero San Luca",
    "Ospedale Regionale San Michele",
    "Policlinico Universitario San Matteo",
]
HOSPITAL_COLORS = [
    ((0.05, 0.36, 0.45), (0.85, 0.94, 0.95)),  # teal
    ((0.12, 0.25, 0.55), (0.88, 0.91, 0.97)),  # blue
    ((0.55, 0.10, 0.15), (0.97, 0.90, 0.90)),  # dark red
    ((0.10, 0.42, 0.22), (0.89, 0.95, 0.90)),  # green
    ((0.35, 0.20, 0.50), (0.93, 0.90, 0.96)),  # purple
]
SPECIALTIES = [
    "Medicina Interna",
    "Cardiologia",
    "Nefrologia",
    "Pneumologia",
    "Neurologia",
    "Gastroenterologia",
    "Endocrinologia",
    "Radiologia",
    "Medicina d'Urgenza",
    "Patologia Clinica",
    "Geriatria",
]


class IdentityFactory:
    def __init__(self, seed: int):
        self.rnd = random.Random(seed)
        self.fake = Faker("it_IT")
        self.fake.seed_instance(seed)

    # -- helpers -------------------------------------------------------------
    def mobile(self) -> str:
        p = self.rnd.choice(
            [
                "320",
                "328",
                "333",
                "338",
                "339",
                "340",
                "347",
                "348",
                "349",
                "366",
                "389",
                "392",
            ]
        )
        n = f"{self.rnd.randint(0, 9999999):07d}"
        fmt = self.rnd.choice(
            ["{p} {a} {b}", "{p}{a}{b}", "{p}-{a}{b}", "+39 {p} {a}{b}", "{p}/{a}{b}"]
        )
        return fmt.format(p=p, a=n[:3], b=n[3:])

    def health_card(self) -> str:
        return "80380" + "".join(str(self.rnd.randint(0, 9)) for _ in range(15))

    def id_document(self) -> str:
        kind = self.rnd.choice(["CI", "PP", "PAT"])
        if kind == "CI":
            return (
                "CA"
                + f"{self.rnd.randint(0, 99999):05d}"
                + "".join(self.rnd.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2))
            )
        if kind == "PP":
            return (
                "Y"
                + "".join(self.rnd.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(1))
                + f"{self.rnd.randint(0, 9999999):07d}"
            )
        return (
            self.rnd.choice(["MI", "RM", "TO", "NA"])
            + f"{self.rnd.randint(0, 9999999):07d}"
            + self.rnd.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
        )

    def cf_for(self, first: str, last: str, sex: str, dob: date, place: str) -> str:
        return codicefiscale.encode(
            lastname=last,
            firstname=first,
            gender=sex,
            birthdate=dob.strftime("%d/%m/%Y"),
            birthplace=place,
        )

    # -- entities -------------------------------------------------------------
    def person(
        self, min_age=18, max_age=95, sex=None, last=None, ref: date | None = None
    ) -> Person:
        ref = ref or date(2026, 10, 1)
        sex = sex or self.rnd.choice("MF")
        first = (
            self.fake.first_name_male() if sex == "M" else self.fake.first_name_female()
        )
        if last is None:
            last = (
                self.rnd.choice(list(EPONYM_SURNAMES))
                if self.rnd.random() < 0.15
                else self.fake.last_name()
            )
        age = self.rnd.randint(min_age, max_age)
        dob = ref - timedelta(days=age * 365 + self.rnd.randint(0, 364))
        bcity, bprov, *_ = self.rnd.choice(CITIES)
        rcity, rprov, rcap, _ = self.rnd.choice(CITIES)
        street = f"{self.rnd.choice(STREET_PREFIX)} {self.rnd.choice(STREET_NAMES)}"
        civic = str(self.rnd.randint(1, 180)) + self.rnd.choice(
            ["", "", "", "/A", "/B", " bis"]
        )
        email = f"{ascii_lower(first)}{self.rnd.choice(['.', '_', ''])}{ascii_lower(last)}{self.rnd.choice(['', str(dob.year)[2:], str(self.rnd.randint(1, 99))])}@{self.rnd.choice(MAIL_DOMAINS)}"
        return Person(
            first=first,
            last=last,
            sex=sex,
            dob=dob,
            birthplace=bcity,
            birth_prov=bprov,
            cf=self.cf_for(first, last, sex, dob, bcity),
            street=street,
            civic=civic,
            cap=rcap,
            city=rcity,
            prov=rprov,
            phone=self.mobile(),
            email=email,
            health_card=self.health_card(),
            id_document=self.id_document(),
        )

    def doctor(self, specialty=None, prov="MI") -> Doctor:
        sex = self.rnd.choice("MF")
        first = (
            self.fake.first_name_male() if sex == "M" else self.fake.first_name_female()
        )
        last = self.fake.last_name()
        prof = self.rnd.random() < 0.2
        title = (
            ("Prof." if sex == "M" else "Prof.ssa")
            if prof
            else ("Dott." if sex == "M" else "Dott.ssa")
        )
        dob = date(
            self.rnd.randint(1955, 1992),
            self.rnd.randint(1, 12),
            self.rnd.randint(1, 28),
        )
        city, *_ = self.rnd.choice(CITIES)
        email_dom = self.rnd.choice(
            ["pec.omceo.it", "asst.it", "ospedale.it", "gmail.com"]
        )
        return Doctor(
            first=first,
            last=last,
            sex=sex,
            title=title,
            specialty=specialty or self.rnd.choice(SPECIALTIES),
            omceo=f"OMCeO {prov} n. {self.rnd.randint(10000, 49999)}",
            cf=self.cf_for(first, last, sex, dob, city),
            phone=self.mobile(),
            email=f"{ascii_lower(first)}.{ascii_lower(last)}@{email_dom}",
        )

    def hospital(self) -> Hospital:
        city, prov, cap, pfx = self.rnd.choice(CITIES)
        color, accent = self.rnd.choice(HOSPITAL_COLORS)
        name = self.rnd.choice(HOSPITAL_NAMES)
        n = self.rnd.randint(0, 9999)
        hospital = Hospital(
            name=name,
            asst=f"ASST {city} {self.rnd.choice(['Nord', 'Sud', 'Centro', 'Est', 'Ovest'])}",
            city=city,
            prov=prov,
            address=f"{self.rnd.choice(['Via', 'Viale', 'Piazzale'])} {self.rnd.choice(STREET_NAMES)}, {self.rnd.randint(1, 60)}",
            cap=cap,
            phone=f"{pfx} {n:04d}{self.rnd.randint(100, 999)}",
            fax=f"{pfx} {n:04d}{self.rnd.randint(100, 999)}",
            web=f"www.{ascii_lower(name.split()[-1])}{ascii_lower(city)}.it",
            color=color,
            accent=accent,
        )
        self.rnd.random()  # former font draw, kept so existing seeds give the same documents
        return hospital

    def nosologico(self, year=2026) -> str:
        return f"{year}{self.rnd.randint(0, 999999):06d}"

    def nre(self) -> str:
        return f"{self.rnd.choice(['030', '120', '150', '080', '090'])}0A{self.rnd.randint(0, 999999999):09d}"
