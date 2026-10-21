"""Builders for the seven synthetic clinical document types.

Each builder returns a ``Built``: either a flowable story rendered with
per-page decorations, or a canvas function for form-like documents.
PII is always written through the registry (``M``), never as plain text.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, Paragraph, Spacer, Table, TableStyle

from anonymization_eval.dataset import content as C
from anonymization_eval.dataset import visuals as V
from anonymization_eval.dataset.identities import (
    CITIES,
    Doctor,
    IdentityFactory,
    Person,
)
from anonymization_eval.dataset.layout import (
    GREY,
    PageCtx,
    draw_segments,
    families,
    fonts,
    styles,
)
from anonymization_eval.dataset.registry import Registry

FMT = "%d/%m/%Y"


@dataclass
class Built:
    doc_type: str
    title: str
    family: str
    story: list | None = None
    ctx: PageCtx | None = None
    canvas_fn: Callable | None = None
    metadata: dict = field(default_factory=dict)


# ----------------------------------------------------------------- helpers ---
def d(x: date) -> str:
    return x.strftime(FMT)


def rand_date(rnd: random.Random, start=date(2026, 1, 10), span=260) -> date:
    return start + timedelta(days=rnd.randint(0, span))


def img_flow(reg: Registry, label: str, img, width_pt: float, note: str = "") -> Image:
    w, h = reg.image(label, *img.size, note=note)
    img = V.fit(img, w, h)
    return Image(V.to_png(img), width=width_pt, height=width_pt * h / w, mask="auto")


def info_table(rows: list[tuple[str, str, str, str]], st: dict, accent) -> Table:
    data = [
        [
            Paragraph(a, st["cellb"]),
            Paragraph(b, st["cell"]),
            Paragraph(c, st["cellb"]),
            Paragraph(dd, st["cell"]),
        ]
        for a, b, c, dd in rows
    ]
    t = Table(data, colWidths=[78, 177, 78, 177])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), Color(*accent)),
                ("BOX", (0, 0), (-1, -1), 0.6, HexColor("#9aa5ad")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, HexColor("#c4ccd2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def grid_table(
    header: list[str], rows: list[list], st: dict, widths: list[float], band
) -> Table:
    data = [[Paragraph(f'<font color="white">{h}</font>', st["cellb"]) for h in header]]
    data += [
        [c if not isinstance(c, str) else Paragraph(c, st["cell"]) for c in r]
        for r in rows
    ]
    t = Table(data, colWidths=widths, repeatRows=1)
    sty = [
        ("BACKGROUND", (0, 0), (-1, 0), Color(*band)),
        ("GRID", (0, 0), (-1, -1), 0.3, HexColor("#b9c2c8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            sty.append(("BACKGROUND", (0, i), (-1, i), HexColor("#f3f5f7")))
    t.setStyle(TableStyle(sty))
    return t


def signature_block(
    reg: Registry,
    rnd: random.Random,
    st: dict,
    role: str,
    doc: Doctor,
    stamp_too: bool = True,
    extra: str = "",
) -> KeepTogether:
    M = reg.mark
    left = [
        Paragraph(role, st["italic"]),
        Paragraph(M(doc.full, "PERSON"), st["cellb"]),
    ]
    if extra:
        left.append(Paragraph(extra, st["small"]))
    sig = img_flow(
        reg,
        "SIGNATURE",
        V.signature(rnd, rnd.randint(380, 460), rnd.randint(110, 150)),
        120,
        f"firma {doc.name}",
    )
    cells = [left, sig]
    widths = [190, 140]
    if stamp_too:
        lines = [doc.full, f"Medico Chirurgo – {doc.specialty}", doc.omceo]
        if rnd.random() < 0.5:
            lines.append(f"C.F. {doc.cf}")
        cells.append(
            img_flow(
                reg,
                "STAMP",
                V.stamp(rnd, lines, rnd.randint(500, 560), rnd.randint(190, 230)),
                135,
                "timbro " + " | ".join(lines),
            )
        )
        widths.append(150)
    t = Table([cells], colWidths=widths)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return KeepTogether([Spacer(1, 10), t])


def name_forms(p: Person, M):
    """Lazy name variants: a mention is registered only when the form is used."""
    forms = [p.full, f"{p.title} {p.last}", p.initial_form, p.full_rev_upper]
    return lambda i: M(forms[i], "PERSON")


def eponym_trap(p: Person) -> str:
    return C.EPONYM_SENTENCES.get(p.last, "")


def hospital_ctx(
    f: IdentityFactory,
    reg: Registry,
    rnd,
    patient: Person,
    ward: str,
    record_id: str,
    record_label: str,
    family: str,
) -> PageCtx:
    h = f.hospital()
    ts = rand_date(rnd)
    return PageCtx(
        kind="hospital",
        family=family,
        patient=patient,
        reg=reg,
        rnd=rnd,
        hospital=h,
        ward=ward,
        director=f.doctor(ward, h.prov) if rnd.random() < 0.8 else None,
        record_id=record_id,
        record_label=record_label,
        printed_by=f.doctor(prov=h.prov) if rnd.random() < 0.6 else None,
        print_ts=f"{d(ts)} {rnd.randint(7, 19):02d}:{rnd.randint(0, 59):02d}",
        print_id=f"PR{rnd.randint(100000, 999999)}",
        hidden_trap=rnd.random() < 0.35,
        accent=h.accent,
        band=h.color,
    )


def mmg_ctx(
    f: IdentityFactory, reg: Registry, rnd, patient: Person, family: str
) -> PageCtx:
    mmg = f.doctor("Medicina Generale")
    street = f"{rnd.choice(['Via', 'Viale', 'Piazza'])} {rnd.choice(['Mazzini', 'Cavour', 'Verdi', 'Carducci', 'Volta'])} {rnd.randint(1, 90)}"
    city, prov, cap, _ = rnd.choice(CITIES)
    accent = rnd.choice([(0.1, 0.35, 0.55), (0.2, 0.45, 0.35), (0.45, 0.25, 0.15)])
    return PageCtx(
        kind="mmg",
        family=family,
        patient=patient,
        reg=reg,
        rnd=rnd,
        mmg=mmg,
        mmg_address=(street, cap, city),
        hidden_trap=rnd.random() < 0.2,
        accent=accent,
        band=accent,
        extras={"mmg_code": f"{rnd.randint(100000, 999999)}", "mmg_city": city},
    )


def metadata(
    reg: Registry, title: str, author: Doctor | None, p: Person, keywords: str = ""
) -> dict:
    md = {
        "title": f"{title} - {p.last} {p.first}",
        "author": author.name if author else "",
        "subject": p.cf,
        "keywords": keywords,
    }
    reg.hidden(f"{p.last} {p.first}", "PERSON", "metadata")
    reg.hidden(p.cf, "FISCAL_CODE", "metadata")
    if author:
        reg.hidden(author.name, "PERSON", "metadata")
    if keywords:
        reg.hidden(keywords, "MEDICAL_RECORD", "metadata")
    return md


# --------------------------------------------------------------- builders ---
def lettera_dimissione(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    sc = rnd.choice(C.SCENARIOS)
    p = f.person(min_age=45)
    fam = rnd.choice(families())
    noso = f.nosologico()
    ctx = hospital_ctx(f, reg, rnd, p, sc["ward"], noso, "Nosologico", fam)
    st = styles(fam, ctx.band)
    mmg, doc, cons = f.doctor("Medicina Generale"), f.doctor(sc["ward"]), f.doctor()
    adm = rand_date(rnd)
    dis = adm + timedelta(days=rnd.randint(4, 16))
    rel_sex = rnd.choice("MF")
    rel = f.person(sex=rel_sex, last=p.last if rnd.random() < 0.5 else None)
    relation = rnd.choice(C.RELATIONS_M if rel_sex == "M" else C.RELATIONS_F)
    names = name_forms(p, M)

    s = [Paragraph("LETTERA DI DIMISSIONE", st["title"])]
    s.append(
        info_table(
            [
                (
                    "Paziente",
                    M(p.full_rev_upper, "PERSON"),
                    p.g("Nato il", "Nata il"),
                    f"{M(d(p.dob), 'DATE_OF_BIRTH')} a {M(p.birthplace, 'LOCATION')} ({M(p.birth_prov, 'LOCATION')})",
                ),
                (
                    "Codice fiscale",
                    M(p.cf, "FISCAL_CODE"),
                    "Tessera sanitaria",
                    M(p.health_card, "HEALTH_CARD"),
                ),
                (
                    "Residenza",
                    f"{M(p.address, 'ADDRESS')}, {M(p.cap, 'ADDRESS')} {M(p.city, 'LOCATION')}",
                    "Telefono",
                    M(p.phone, "PHONE"),
                ),
                (
                    "Ricovero",
                    f"dal {M(d(adm), 'DATE')} al {M(d(dis), 'DATE')}",
                    "Nosologico",
                    M(noso, "MEDICAL_RECORD"),
                ),
                ("Medico curante", M(mmg.full, "PERSON"), "Reparto", sc["ward"]),
            ],
            st,
            ctx.accent,
        )
    )
    s += [
        Spacer(1, 8),
        Paragraph("Gentile Collega,", st["body"]),
        Paragraph(
            f"si dimette in data odierna {names(1)}, di anni {M(str(p.age(dis)), 'AGE')}, "
            f"{p.g('ricoverato', 'ricoverata')} presso la nostra U.O. il {M(d(adm), 'DATE')} per il quadro di seguito descritto.",
            st["body"],
        ),
    ]
    s += [
        Paragraph("Diagnosi alla dimissione", st["h"]),
        Paragraph(sc["diagnosis"], st["body"]),
        Paragraph("Comorbidità: " + "; ".join(sc["secondary"]) + ".", st["body"]),
    ]
    hist = sc["history"] + " " + eponym_trap(p)
    hist += (
        f" Vive con {relation} {M(rel.full, 'PERSON')} (tel. {M(rel.phone, 'PHONE')}), "
        f"che collabora all'assistenza domiciliare."
    )
    s += [Paragraph("Anamnesi", st["h"]), Paragraph(hist, st["body"])]
    s.append(Paragraph("Decorso clinico", st["h"]))
    for i, para in enumerate(sc["course"]):
        if i == 1:
            para += f" {p.g('Il paziente', 'La paziente')} {names(0)} ha mostrato buona aderenza alle terapie."
        s.append(Paragraph(para, st["body"]))
    s.append(
        Paragraph(
            f"Eseguita consulenza {cons.specialty.lower()} ({M(cons.full, 'PERSON')}) con indicazioni recepite. "
            + rnd.choice(C.GENERIC_EPONYMS),
            st["body"],
        )
    )
    s.append(Paragraph("Esami ematici alla dimissione", st["h"]))
    rows = []
    for name, val, unit, ref, flag in sc["labs"]:
        col = "#c0392b" if flag == "H" else "#1f5fa8" if flag == "L" else None
        v = f'<font color="{col}"><b>{val} {flag}</b></font>' if col else val
        rows.append([name, v, unit, ref])
    s.append(
        grid_table(
            ["Esame", "Risultato", "U.M.", "Riferimento"],
            rows,
            st,
            [170, 90, 90, 160],
            ctx.band,
        )
    )
    s.append(Paragraph("Terapia alla dimissione", st["h"]))
    s.append(
        grid_table(
            ["Farmaco", "Posologia", "Orario"],
            [list(r) for r in sc["therapy"]],
            st,
            [220, 150, 140],
            ctx.band,
        )
    )
    h = ctx.hospital
    s += [
        Paragraph("Indicazioni e follow-up", st["h"]),
        Paragraph(
            sc["followup"]
            + f" Per informazioni contattare il reparto al numero {M(h.phone, 'PHONE', required=False)} "
            f"oppure scrivere a {M('reparto.' + sc['ward'].split()[0].lower() + '@' + h.web[4:], 'EMAIL', required=False)}.",
            st["body"],
        ),
    ]
    s.append(signature_block(reg, rnd, st, "Il Medico di reparto", doc))
    if ctx.director:
        s.append(
            Paragraph(
                f"Il Direttore U.O.C.: {M(ctx.director.full, 'PERSON')}", st["small"]
            )
        )
    return Built(
        "lettera_dimissione",
        "Lettera di dimissione",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Lettera dimissione", doc, p, noso),
    )


def referto_laboratorio(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    p = f.person()
    fam = rnd.choice(families())
    sample = f"{rnd.randint(26, 26)}{rnd.randint(1000000, 9999999)}"
    ctx = hospital_ctx(
        f,
        reg,
        rnd,
        p,
        "Patologia Clinica – Laboratorio Analisi",
        sample,
        "Campione",
        fam,
    )
    st = styles(fam, ctx.band)
    req, val = f.doctor(), f.doctor("Patologia Clinica")
    draw = rand_date(rnd)
    s = [Paragraph("REFERTO DI LABORATORIO", st["title"])]
    s.append(
        info_table(
            [
                (
                    "Paziente",
                    M(p.full_rev_upper, "PERSON"),
                    "Sesso / Età",
                    f"{p.sex} / {M(str(p.age(draw)), 'AGE')} anni",
                ),
                (
                    p.g("Nato il", "Nata il"),
                    f"{M(d(p.dob), 'DATE_OF_BIRTH')} a {M(p.birthplace, 'LOCATION')}",
                    "Codice fiscale",
                    M(p.cf, "FISCAL_CODE"),
                ),
                (
                    "Richiesta n.",
                    M(f"RQ{rnd.randint(1000000, 9999999)}", "MEDICAL_RECORD"),
                    "Campione",
                    M(sample, "MEDICAL_RECORD"),
                ),
                (
                    "Data prelievo",
                    f"{M(d(draw), 'DATE')} ore {rnd.randint(7, 10):02d}:{rnd.randint(0, 59):02d}",
                    "Data referto",
                    M(d(draw + timedelta(days=1)), "DATE"),
                ),
                (
                    "Medico richiedente",
                    M(req.full, "PERSON"),
                    "Provenienza",
                    rnd.choice(
                        ["Ambulatorio esterno", "Medicina Interna", "Cardiologia"]
                    ),
                ),
            ],
            st,
            ctx.accent,
        )
    )
    panels = rnd.sample(list(C.LAB_PANELS), k=rnd.randint(3, 5))
    for panel in ["EMATOLOGIA", "CHIMICA CLINICA"] + [
        x for x in panels if x not in ("EMATOLOGIA", "CHIMICA CLINICA")
    ]:
        s.append(Paragraph(panel, st["h"]))
        rows = []
        for name, unit, (lo, hi) in C.LAB_PANELS[panel]:
            span = hi - lo
            v = (
                rnd.uniform(lo, hi)
                if rnd.random() > 0.25
                else rnd.choice(
                    [
                        lo - span * rnd.uniform(0.05, 0.4),
                        hi + span * rnd.uniform(0.05, 0.6),
                    ]
                )
            )
            v = max(v, 0)
            txt = f"{v:.0f}" if hi >= 100 else f"{v:.2f}".replace(".", ",")
            flag = "H" if v > hi else "L" if v < lo else ""
            col = "#c0392b" if flag == "H" else "#1f5fa8" if flag == "L" else None
            cell = f'<font color="{col}"><b>{txt}</b></font>' if col else txt
            ref = f"{lo} - {hi}".replace(".", ",")
            rows.append([name, cell, flag, unit, ref])
        s.append(
            grid_table(
                ["Esame", "Risultato", "", "U.M.", "Valori di riferimento"],
                rows,
                st,
                [170, 80, 25, 85, 150],
                ctx.band,
            )
        )
    if rnd.random() < 0.6:
        rel = f.person()
        s.append(Spacer(1, 6))
        s.append(
            Paragraph(
                f"Note: referto ritirato da {M(rel.full, 'PERSON')}, delegato con documento n. "
                f"{M(rel.id_document, 'ID_DOCUMENT')}.",
                st["italic"],
            )
        )
    s.append(Spacer(1, 8))
    url = f"https://referti.{ctx.hospital.web[4:]}/r?c={sample}&cf={p.cf}"
    qr_img = img_flow(reg, "QRCODE", V.qr(url), 62, url)
    txt = [
        Paragraph(
            f"Referto validato e firmato digitalmente da {M(val.full, 'PERSON')} (specialista in Patologia Clinica) "
            "ai sensi del D.Lgs. 82/2005.",
            st["small"],
        ),
        Paragraph(f"Scarica il referto online: {M(url, 'URL')}", st["small"]),
    ]
    t = Table([[txt, qr_img]], colWidths=[430, 80])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    s.append(KeepTogether([t]))
    return Built(
        "referto_laboratorio",
        "Referto di laboratorio",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Referto laboratorio", val, p, sample),
    )


def referto_radiologico(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    ex = rnd.choice(C.RADIOLOGY)
    p = f.person(min_age=30)
    fam = rnd.choice(families())
    acc = f"ACC{rnd.randint(10000000, 99999999)}"
    ctx = hospital_ctx(
        f,
        reg,
        rnd,
        p,
        "Radiologia Diagnostica e Interventistica",
        acc,
        "Accession",
        fam,
    )
    st = styles(fam, ctx.band)
    rad, presc, tsrm = f.doctor("Radiologia"), f.doctor(), f.person()
    dt = rand_date(rnd)
    s = [Paragraph("REFERTO DI DIAGNOSTICA PER IMMAGINI", st["title"])]
    s.append(
        info_table(
            [
                (
                    "Paziente",
                    M(p.full_rev_upper, "PERSON"),
                    p.g("Nato il", "Nata il"),
                    f"{M(d(p.dob), 'DATE_OF_BIRTH')} ({M(p.birthplace, 'LOCATION')})",
                ),
                (
                    "Codice fiscale",
                    M(p.cf, "FISCAL_CODE"),
                    "Accession n.",
                    M(acc, "MEDICAL_RECORD"),
                ),
                (
                    "Data esame",
                    M(d(dt), "DATE"),
                    "Prescrittore",
                    M(presc.full, "PERSON"),
                ),
                (
                    "Telefono",
                    M(p.phone, "PHONE"),
                    "Impegnativa",
                    M(f.nre(), "PRESCRIPTION_ID"),
                ),
            ],
            st,
            ctx.accent,
        )
    )
    s += [Spacer(1, 6), Paragraph(ex["exam"], st["h"])]
    s += [
        Paragraph(f"<b>Quesito clinico:</b> {ex['question']}", st["body"]),
        Paragraph(f"<b>Tecnica:</b> {ex['technique']}", st["body"]),
    ]
    s.append(Paragraph("Reperto", st["h"]))
    for para in ex["findings"]:
        s.append(Paragraph(para, st["body"]))
    if eponym_trap(p):
        s.append(Paragraph(eponym_trap(p), st["body"]))
    s += [
        Paragraph("Conclusioni", st["h"]),
        Paragraph(f"<b>{ex['conclusion']}</b>", st["body"]),
    ]
    s.append(
        Paragraph(
            f"Dose: DLP {rnd.randint(300, 1200)} mGy·cm. Esame eseguito dal TSRM {M(tsrm.full, 'PERSON')}.",
            st["small"],
        )
    )
    s.append(
        signature_block(
            reg, rnd, st, "Il Medico Radiologo", rad, stamp_too=rnd.random() < 0.5
        )
    )
    return Built(
        "referto_radiologico",
        "Referto radiologico",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Referto RX", rad, p, acc),
    )


def verbale_ps(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    case = rnd.choice(C.PS_CASES)
    p = f.person()
    fam = rnd.choice(families())
    acc = f"PS{rnd.randint(2026000000, 2026999999)}"
    ctx = hospital_ctx(
        f, reg, rnd, p, "Pronto Soccorso e Medicina d'Urgenza", acc, "Accesso", fam
    )
    st = styles(fam, ctx.band)
    doc = f.doctor("Medicina d'Urgenza")
    dt = rand_date(rnd)
    rel_sex = rnd.choice("MF")
    rel = f.person(sex=rel_sex)
    relation = rnd.choice(C.RELATIONS_M if rel_sex == "M" else C.RELATIONS_F)
    s = [Paragraph("VERBALE DI PRONTO SOCCORSO", st["title"])]
    s.append(
        info_table(
            [
                (
                    "Accesso n.",
                    M(acc, "MEDICAL_RECORD"),
                    "Arrivo",
                    f"{M(d(dt), 'DATE')} ore {case['tx'][0][0][4:]}",
                ),
                (
                    "Paziente",
                    M(p.full_rev_upper, "PERSON"),
                    p.g("Nato il", "Nata il"),
                    f"{M(d(p.dob), 'DATE_OF_BIRTH')} a {M(p.birthplace, 'LOCATION')}",
                ),
                (
                    "Codice fiscale",
                    M(p.cf, "FISCAL_CODE"),
                    "Documento",
                    f"CI n. {M(p.id_document, 'ID_DOCUMENT')}",
                ),
                (
                    "Residenza",
                    f"{M(p.address, 'ADDRESS')} – {M(p.city, 'LOCATION')}",
                    "Telefono",
                    M(p.phone, "PHONE"),
                ),
                (
                    "Accompagnato da",
                    f"{M(rel.full, 'PERSON')} ({relation.split()[-1]})",
                    "Tel. accompagnatore",
                    M(rel.phone, "PHONE"),
                ),
            ],
            st,
            ctx.accent,
        )
    )
    code, rgb = case["triage"]
    tri = Table(
        [
            [
                Paragraph("<b>CODICE TRIAGE</b>", st["cell"]),
                Paragraph(f'<font color="white"><b>{code}</b></font>', st["cell"]),
                Paragraph(
                    f"Modalità di arrivo: {rnd.choice(['118', 'mezzi propri', 'ambulanza privata'])}",
                    st["cell"],
                ),
            ]
        ],
        colWidths=[100, 110, 300],
    )
    tri.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (1, 0), (1, 0), Color(*rgb)),
                ("BOX", (0, 0), (-1, -1), 0.5, GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    s += [Spacer(1, 6), tri]
    s += [
        Paragraph("Motivo dell'accesso", st["h"]),
        Paragraph(case["complaint"], st["body"]),
        Paragraph("Esame obiettivo", st["h"]),
        Paragraph(case["exam"] + " " + rnd.choice(C.GENERIC_EPONYMS), st["body"]),
        Paragraph("Accertamenti", st["h"]),
        Paragraph(case["tests"], st["body"]),
        Paragraph("Terapia in Pronto Soccorso", st["h"]),
        grid_table(
            ["Ora", "Somministrazione"],
            [list(x) for x in case["tx"]],
            st,
            [80, 430],
            ctx.band,
        ),
        Paragraph("Diagnosi", st["h"]),
        Paragraph(f"<b>{case['dx']}</b>", st["body"]),
        Paragraph("Esito", st["h"]),
        Paragraph(
            case["outcome"] + f" Informato {relation} {M(rel.first, 'PERSON')}.",
            st["body"],
        ),
    ]
    s.append(
        signature_block(
            reg, rnd, st, "Il Medico di Pronto Soccorso", doc, stamp_too=False
        )
    )
    pt_sig = img_flow(
        reg,
        "SIGNATURE",
        V.signature(rnd, rnd.randint(360, 420), rnd.randint(100, 130)),
        110,
        f"firma {p.full}",
    )
    t = Table(
        [
            [
                Paragraph(
                    f"Firma {p.g('del paziente', 'della paziente')} per ricevuta copia",
                    st["italic"],
                ),
                pt_sig,
            ]
        ],
        colWidths=[200, 140],
    )
    s.append(t)
    return Built(
        "verbale_ps",
        "Verbale di Pronto Soccorso",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Verbale PS", doc, p, acc),
    )


def lettera_invio(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    spec, reason = rnd.choice(C.REFERRAL_REASONS)
    p = f.person(min_age=35)
    fam = rnd.choice(families())
    ctx = mmg_ctx(f, reg, rnd, p, fam)
    st = styles(fam, ctx.band)
    mmg = ctx.mmg
    dt = rand_date(rnd)
    sc = rnd.choice(C.SCENARIOS)
    names = name_forms(p, M)
    s = [
        Spacer(1, 4),
        Paragraph(
            f"{M(ctx.extras['mmg_city'], 'LOCATION')}, {M(d(dt), 'DATE')}", st["body"]
        ),
        Spacer(1, 6),
        Paragraph(f"<b>Alla c.a. dello Specialista in {spec}</b>", st["body"]),
        Spacer(1, 8),
        Paragraph("Oggetto: richiesta di valutazione specialistica", st["h"]),
        Paragraph("Gentile Collega,", st["body"]),
    ]
    s.append(
        Paragraph(
            f"invio alla Sua cortese attenzione {names(1)} ({M(p.full, 'PERSON')}), {p.g('nato', 'nata')} a "
            f"{M(p.birthplace, 'LOCATION')} il {M(d(p.dob), 'DATE_OF_BIRTH')}, C.F. {M(p.cf, 'FISCAL_CODE')}, residente a "
            f"{M(p.city, 'LOCATION')} in {M(p.address, 'ADDRESS')}, per {reason}.",
            st["body"],
        )
    )
    s.append(Paragraph(f"In anamnesi: {sc['history']} {eponym_trap(p)}", st["body"]))
    s.append(
        Paragraph(
            "Terapia domiciliare in atto: "
            + ", ".join(x[0] for x in sc["therapy"][:4])
            + ".",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"Ultimi esami ematici del {M(d(dt - timedelta(days=rnd.randint(10, 60))), 'DATE')}: "
            + "; ".join(f"{a} {b} {c}" for a, b, c, *_ in sc["labs"][:4])
            + ".",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"{p.g('Il paziente', 'La paziente')} è {p.g('reperibile', 'reperibile')} al numero {M(p.phone, 'PHONE')} "
            f"o all'indirizzo {M(p.email, 'EMAIL')}. Tessera sanitaria n. {M(p.health_card, 'HEALTH_CARD')}.",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"Rimango a disposizione per ogni chiarimento al numero {M(mmg.phone, 'PHONE')}. "
            f"Ringrazio per la collaborazione e porgo cordiali saluti.",
            st["body"],
        )
    )
    s.append(signature_block(reg, rnd, st, "Il Medico di Medicina Generale", mmg))
    return Built(
        "lettera_invio",
        "Lettera di invio allo specialista",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Lettera invio", mmg, p),
    )


def certificato(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    M = reg.mark
    reason, days = rnd.choice(C.CERT_REASONS)
    p = f.person(min_age=20, max_age=66)
    fam = rnd.choice(families())
    ctx = mmg_ctx(f, reg, rnd, p, fam)
    st = styles(fam, ctx.band)
    dt = rand_date(rnd)
    s = [Spacer(1, 20), Paragraph("CERTIFICATO MEDICO", st["title"]), Spacer(1, 10)]
    s.append(
        Paragraph(
            f"Si certifica che {p.title} {M(p.full, 'PERSON')}, {p.g('nato', 'nata')} a {M(p.birthplace, 'LOCATION')} "
            f"({M(p.birth_prov, 'LOCATION')}) il {M(d(p.dob), 'DATE_OF_BIRTH')}, residente in {M(p.city, 'LOCATION')}, "
            f"{M(p.address, 'ADDRESS')} – CAP {M(p.cap, 'ADDRESS')}, codice fiscale {M(p.cf, 'FISCAL_CODE')}, "
            f"identificato a mezzo documento n. {M(p.id_document, 'ID_DOCUMENT')}, è {p.g('stato', 'stata')} da me "
            f"{p.g('visitato', 'visitata')} in data odierna e risulta {p.g('affetto', 'affetta')} da {reason}.",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"Si prescrive riposo per giorni {days} (dal {M(d(dt), 'DATE')} al {M(d(dt + timedelta(days=days - 1)), 'DATE')}), "
            "salvo complicazioni.",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"Certificato trasmesso telematicamente all'INPS, protocollo n. {M(str(rnd.randint(10**17, 10**18 - 1)), 'MEDICAL_RECORD')}.",
            st["body"],
        )
    )
    s.append(
        Paragraph(
            f"Si rilascia su richiesta {p.g('dell’interessato', 'dell’interessata')} per gli usi consentiti dalla legge.",
            st["body"],
        )
    )
    s += [
        Spacer(1, 10),
        Paragraph(
            f"{M(ctx.extras['mmg_city'], 'LOCATION')}, {M(d(dt), 'DATE')}", st["body"]
        ),
    ]
    s.append(signature_block(reg, rnd, st, "Il Medico", ctx.mmg))
    return Built(
        "certificato",
        "Certificato medico",
        fam,
        s,
        ctx,
        metadata=metadata(reg, "Certificato", ctx.mmg, p),
    )


def ricetta(f: IdentityFactory, reg: Registry, rnd: random.Random) -> Built:
    """Promemoria della ricetta elettronica: form-like, drawn on canvas."""
    p = f.person()
    fam = rnd.choice(families())
    doc = f.doctor("Medicina Generale")
    nre = f.nre()
    dt = rand_date(rnd)
    drugs = rnd.choice(C.PRESCRIPTIONS)
    reg_name = rnd.choice(
        ["LOMBARDIA", "LAZIO", "PIEMONTE", "VENETO", "EMILIA-ROMAGNA", "PUGLIA"]
    )
    images = {}
    for key, data in (("nre", nre), ("cf", p.cf)):
        img = V.barcode39(data)
        w, h = reg.image("BARCODE", *img.size, note=f"code39 {data}")
        images[key] = V.fit(img, w, h)

    def draw(c, R: Registry):
        reg_f, bold, _, _ = fonts(fam)
        W, H = c._pagesize
        k = HexColor("#000000")
        c.setStrokeColor(HexColor("#333333"))
        c.setLineWidth(1)
        c.rect(30, H - 470, W - 60, 440)
        draw_segments(
            c,
            W / 2,
            H - 50,
            [("SERVIZIO SANITARIO NAZIONALE", None)],
            bold,
            12,
            k,
            "center",
        )
        draw_segments(
            c, W / 2, H - 65, [(f"REGIONE {reg_name}", None)], reg_f, 9, k, "center"
        )
        draw_segments(
            c,
            W / 2,
            H - 80,
            [("RICETTA ELETTRONICA - PROMEMORIA PER L'ASSISTITO", None)],
            bold,
            9,
            k,
            "center",
        )
        for i, (key, label, val, lab) in enumerate(
            (
                ("nre", "NRE", nre, "PRESCRIPTION_ID"),
                ("cf", "CODICE FISCALE", p.cf, "FISCAL_CODE"),
            )
        ):
            x = 50 + i * 270
            img = images[key]
            c.drawImage(ImageReader(V.to_png(img)), x, H - 140, width=220, height=45)
            draw_segments(
                c,
                x,
                H - 152,
                [(f"{label}: ", None), (val, R.color(val, lab))],
                reg_f,
                8.5,
                k,
            )
        y = H - 180
        rows = [
            (
                "COGNOME E NOME/INIZIALI DELL'ASSISTITO:",
                [(f"{p.last.upper()} {p.first.upper()}", "PERSON")],
            ),
            ("INDIRIZZO:", [(p.address.upper(), "ADDRESS")]),
            ("CAP:", [(p.cap, "ADDRESS")]),
            ("CITTA':", [(p.city.upper(), "LOCATION")]),
            ("PROV:", [(p.prov, "LOCATION")]),
            ("ESENZIONE:", []),
            ("TIPOLOGIA PRESCRIZIONE:", []),
        ]
        extra = {
            "ESENZIONE:": rnd.choice(["NON ESENTE", "E01", "048", "013"]),
            "TIPOLOGIA PRESCRIZIONE:": "S",
        }
        for lab, vals in rows:
            segs = [(lab + " ", None)]
            for v, code in vals:
                segs.append((v, R.color(v, code)))
            if lab in extra:
                segs.append((extra[lab], None))
            draw_segments(c, 45, y, segs, reg_f, 8.5, k)
            y -= 15
        y -= 5
        c.line(40, y + 8, W - 40, y + 8)
        draw_segments(c, 45, y - 5, [("PRESCRIZIONE", None)], bold, 8.5, k)
        draw_segments(c, 420, y - 5, [("QTA", None)], bold, 8.5, k)
        draw_segments(c, 470, y - 5, [("NOTA", None)], bold, 8.5, k)
        y -= 20
        for drug, q in drugs:
            draw_segments(c, 45, y, [(drug, None)], reg_f, 8.5, k)
            draw_segments(c, 425, y, [(q, None)], reg_f, 8.5, k)
            y -= 14
        y = H - 420
        c.line(40, y + 15, W - 40, y + 15)
        draw_segments(
            c, 45, y, [("DATA: ", None), (d(dt), R.color(d(dt), "DATE"))], reg_f, 8.5, k
        )
        draw_segments(
            c,
            45,
            y - 14,
            [
                ("COGNOME E NOME DEL MEDICO: ", None),
                (
                    f"{doc.last.upper()} {doc.first.upper()}",
                    R.color(f"{doc.last.upper()} {doc.first.upper()}", "PERSON"),
                ),
            ],
            reg_f,
            8.5,
            k,
        )
        draw_segments(
            c,
            45,
            y - 28,
            [
                ("CODICE FISCALE DEL MEDICO: ", None),
                (doc.cf, R.color(doc.cf, "FISCAL_CODE")),
            ],
            reg_f,
            8.5,
            k,
        )
        draw_segments(
            c,
            330,
            y,
            [
                ("CODICE AUTENTICAZIONE: ", None),
                (f"{rnd.randint(100000, 999999)}", None),
            ],
            reg_f,
            8.5,
            k,
        )
        draw_segments(
            c,
            45,
            H - 490,
            [
                (
                    "Il promemoria va consegnato al farmacista. Ricetta rilasciata ai sensi del DM 2/11/2011.",
                    None,
                )
            ],
            reg_f,
            7,
            GREY,
        )

    return Built(
        "ricetta",
        "Promemoria ricetta elettronica",
        fam,
        canvas_fn=draw,
        metadata=metadata(reg, "Ricetta", doc, p, nre),
    )


BUILDERS = {
    "lettera_dimissione": lettera_dimissione,
    "referto_laboratorio": referto_laboratorio,
    "referto_radiologico": referto_radiologico,
    "verbale_ps": verbale_ps,
    "lettera_invio": lettera_invio,
    "certificato": certificato,
    "ricetta": ricetta,
}
