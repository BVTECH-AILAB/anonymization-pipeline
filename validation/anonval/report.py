"""Markdown report of a validation run (Italian, ``it-IT`` number format)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from anonval.scoring import DocScore, Hit, ValueOutcome
from anonval.settings import Thresholds

LOCATIONS = {
    "letterhead": "intestazione",
    "running_header": "intestazione ripetuta",
    "footer": "piè di pagina",
    "fax_line": "riga fax",
    "patient_box": "riquadro paziente",
    "fields": "campi",
    "table": "tabella",
    "paragraph": "paragrafo",
    "char_boxes": "caselle per carattere",
    "barcode": "codice a barre",
    "signature": "firma",
    "stamp": "timbro",
    "note": "nota",
    "footnotes": "note a piè di pagina",
    "address": "destinatario",
    "title": "titolo",
    "heading": "sottotitolo",
    "section": "banda di sezione",
    "checkboxes": "caselle di spunta",
    "body": "corpo",
}
# detectors of the service (report.entities[].sources)
SOURCES = {
    "detection": "VLM",
    "verification": "verifica VLM",
    "rules": "regole",
    "labels": "etichette",
    "ner": "NER",
}
DIFFICULTIES = ("facile", "media", "complessa")
STATUS = {
    "completed": "OK",
    "rejected": "RESPINTO",
    "error": "ERRORE",
    "timeout": "TIMEOUT",
    "cancelled": "ANNULLATO",
}
# verdict key -> label, value in the overall metrics, operator, threshold of config.yaml
THRESHOLD_LABELS = {
    "recall_min": ("Recall", ("recall",), ">=", "recall_min"),
    "tasso_fuga_max": ("Tasso di fuga", ("tasso_fuga",), "<=", "tasso_fuga_max"),
    "tasso_fuga_layout_max": (
        "Tasso di fuga nel PDF con layout",
        ("layout", "tasso_fuga"),
        "<=",
        "tasso_fuga_max",
    ),
    "precision_min": ("Precision", ("precision",), ">=", "precision_min"),
    "f1_min": ("F1", ("f1",), ">=", "f1_min"),
}


def _metric(overall: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = overall
    for key in path:
        value = (value or {}).get(key)
    return value


def _layout_rate(overall: dict[str, Any]) -> str:
    """Leak rate in the layout PDF, "non richiesto" or "non prodotto"."""
    layout = overall.get("layout")
    if not layout:
        return "non richiesto"
    if not layout["documenti_verificati"]:
        return "non prodotto"
    return pct(layout["tasso_fuga"])


@dataclass
class ProfileRun:
    """Outcome of one profile: parameters sent, metrics, per-document scores."""

    name: str
    description: str
    form: dict[str, str]
    summary: dict[str, Any]
    verdict: dict[str, bool | None]
    docs: list[DocScore] = field(default_factory=list)
    aborted: str | None = None

    @property
    def passed(self) -> bool | None:
        if self.aborted:
            return False
        values = list(self.verdict.values())
        if any(v is False for v in values):
            return False
        return None if all(v is None for v in values) else True


def pct(value: float | None) -> str:
    """``0.9523`` -> ``95,2%``; None -> ``n.d.``."""
    return "n.d." if value is None else f"{value * 100:.1f}%".replace(".", ",")


def num(value: float | None) -> str:
    return "n.d." if value is None else f"{value:.1f}".replace(".", ",")


def cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _outcome(passed: bool | None) -> str:
    return {True: "PASS", False: "FAIL", None: "n.d."}[passed]


def _summary_rows(runs: list[ProfileRun]) -> list[list[Any]]:
    rows = []
    for run in runs:
        o = run.summary["overall"]
        d = o["documenti"]
        rows.append(
            [
                f"`{run.name}`",
                _outcome(run.passed),
                f"{d['completati']}/{d['respinti']}/{d['errori']}",
                pct(o["recall"]),
                pct(o["tasso_fuga"]),
                _layout_rate(o),
                pct(o["precision"]),
                pct(o["f1"]),
                o["documenti_con_fughe"],
                o["negativi_violati"],
                num(o["tempi_s"]["medio"]),
                num(o["tempi_s"]["p95"]),
            ]
        )
    return rows


def _comparison(runs: list[ProfileRun]) -> str:
    base = runs[0].summary["overall"]
    metrics = [
        ("Recall", "recall"),
        ("Tasso di fuga", "tasso_fuga"),
        ("Precision", "precision"),
        ("F1", "f1"),
    ]
    rows = []
    for run in runs[1:]:
        o = run.summary["overall"]
        row: list[Any] = [f"`{run.name}`"]
        for _, key in metrics:
            if o[key] is None or base[key] is None:
                row.append("n.d.")
            else:
                delta = (o[key] - base[key]) * 100
                row.append(f"{delta:+.1f} pp".replace(".", ","))
        rows.append(row)
    header = ["Profilo", *(label for label, _ in metrics)]
    return (
        f"Differenze rispetto al profilo `{runs[0].name}` (punti percentuali).\n\n"
        + table(header, rows)
    )


def _stats_row(name: str, s: dict[str, Any]) -> list[Any]:
    detected = s["rilevati"]
    return [
        name,
        s["attesi"],
        s["rilevati"],
        pct(s["recall"]),
        s["fughe"],
        pct(s["tasso_fuga"]),
        pct(s["categoria_esatta"] / detected if detected else None),
        s["non_estratti"],
    ]


STATS_HEADER = [
    "",
    "Attesi",
    "Rilevati",
    "Recall",
    "Fughe",
    "Tasso fuga",
    "Cat. esatta",
    "Non estratti",
]


def _profile(run: ProfileRun, thresholds: Thresholds) -> str:
    out = [f"## Profilo `{run.name}`", ""]
    if run.description:
        out += [run.description, ""]
    params = (
        ", ".join(f"`{k}={v}`" for k, v in run.form.items())
        or "nessuno (default del server)"
    )
    out += [f"Parametri inviati: {params}, `transcript=true`.", ""]
    if run.aborted:
        out += [f"**Profilo interrotto**: {run.aborted}", ""]
    o = run.summary["overall"]

    rows = []
    for key, (label, path, op, limit) in THRESHOLD_LABELS.items():
        if key == "tasso_fuga_layout_max" and not o.get("layout"):
            continue  # layout PDF not requested
        rows.append(
            [
                label,
                pct(_metric(o, path)),
                f"{op} {pct(getattr(thresholds, limit))}",
                _outcome(run.verdict.get(key)),
            ]
        )
    out += ["### Soglie", "", table(["Metrica", "Valore", "Soglia", "Esito"], rows), ""]

    v, occ = o["valori"], o["occorrenze"]
    out += [
        "### Valori e occorrenze",
        "",
        table(
            ["Misura", "Valore"],
            [
                ["Valori attesi (estratti)", v["attesi"]],
                ["Valori rilevati", v["rilevati"]],
                ["Valori rimasti in chiaro (fughe)", v["fughe"]],
                ["Documenti con almeno una fuga", o["documenti_con_fughe"]],
                ["Valori persi dall'estrazione", v["non_estratti"]],
                [
                    "Categoria esatta sui rilevati",
                    pct(
                        v["categoria_esatta"] / v["rilevati"] if v["rilevati"] else None
                    ),
                ],
                [
                    "Categoria della stessa famiglia",
                    pct(
                        v["categoria_famiglia"] / v["rilevati"]
                        if v["rilevati"]
                        else None
                    ),
                ],
                ["Occorrenze censurate", occ["censurate"]],
                ["Occorrenze corrette", occ["corrette"]],
                ["Falsi positivi", occ["falsi_positivi"]],
                ["Precision con categoria compatibile", pct(o["precision_categoria"])],
                ["Censure di categorie disattivate", occ["categorie_disattivate"]],
                ["Negativi noti censurati", o["negativi_violati"]],
                ["Allucinazioni scartate (report)", o["allucinazioni_scartate"]],
                ["Eponimi scartati (report)", o["eponimi_scartati"]],
            ],
        ),
        "",
    ]
    if layout := o.get("layout"):
        out += [
            "### PDF con layout",
            "",
            "Testo del PDF censurato con il layout dell'originale, controllato come il Markdown "
            "(valori di almeno 3 lettere o cifre).",
            "",
            table(
                ["Misura", "Valore"],
                [
                    ["Documenti verificati", layout["documenti_verificati"]],
                    ["PDF richiesti ma non prodotti", layout["pdf_non_prodotti"]],
                    ["Valori verificati", layout["valori_verificati"]],
                    ["Valori rimasti in chiaro nel PDF", layout["fughe"]],
                    ["Tasso di fuga nel PDF", pct(layout["tasso_fuga"])],
                    ["Documenti con fughe nel PDF", layout["documenti_con_fughe"]],
                ],
            ),
            "",
        ]

    by_difficulty = run.summary["by_difficulty"]
    if by_difficulty:
        out += [
            "### Per difficoltà",
            "",
            table(STATS_HEADER, [_stats_row(k, s) for k, s in by_difficulty.items()]),
            "",
        ]

    matrix = run.summary["by_type_difficulty"]
    if matrix:
        rows = []
        for doc_type, cells in matrix.items():
            row: list[Any] = [doc_type]
            for difficulty in DIFFICULTIES:
                s = cells.get(difficulty)
                row.append(f"{pct(s['recall'])} / {s['fughe']}" if s else "-")
            rows.append(row)
        out += [
            "### Tipo x difficoltà",
            "",
            "Recall / numero di fughe.",
            "",
            table(["Tipo", *DIFFICULTIES], rows),
            "",
        ]

    out += [
        "### Per gruppo",
        "",
        table(
            STATS_HEADER, [_stats_row(k, s) for k, s in run.summary["by_group"].items()]
        ),
        "",
    ]
    rows = [
        [*_stats_row(k, s), s.get("falsi_positivi", 0)]
        for k, s in run.summary["by_category"].items()
    ]
    out += ["### Per categoria", "", table([*STATS_HEADER, "Falsi positivi"], rows), ""]
    rows = [
        _stats_row(LOCATIONS.get(k, k), s)
        for k, s in run.summary["by_location"].items()
    ]
    out += ["### Per posizione nel layout", "", table(STATS_HEADER, rows), ""]

    e, ver = o["estrazione"], o["verificatore"]
    vlm = (
        ", ".join(f"{reason or 'n.d.'}: {n}" for reason, n in e["pagine_vlm"].items())
        or "nessuna"
    )
    out += [
        "### Estrazione e verifica",
        "",
        table(
            ["Misura", "Valore"],
            [
                ["Pagine estratte con docling", e["pagine_docling"]],
                ["Pagine passate al VLM (motivo)", vlm],
                [
                    "Documenti con fughe segnalate dal verificatore",
                    ver["fughe_segnalate"],
                ],
                ["Documenti con fughe NON segnalate", ver["fughe_non_segnalate"]],
                [
                    "Documenti con residui segnalati senza fughe reali",
                    ver["falsi_allarmi"],
                ],
                ["Documenti senza fughe e senza residui", ver["senza_fughe"]],
            ],
        ),
        "",
    ]
    out += _details(run)
    return "\n".join(out)


def _pages(pages: list[int]) -> str:
    if not pages:
        return "pagina n.d."
    return ("pag. " if len(pages) == 1 else "pagg. ") + ", ".join(map(str, pages))


def _by(sources: list[str]) -> str:
    return ", ".join(SOURCES.get(s, s) for s in sources)


def _layout(v: ValueOutcome) -> str:
    return ", ".join(LOCATIONS.get(x, x) for x in v.locations)


def _leak(v: ValueOutcome) -> str:
    text = f'"{v.value}" ({v.category}): in chiaro a {_pages(v.leak_pages)}; nel layout: {_layout(v)}'
    if not v.sources:
        return f"{text}; nessun rilevatore lo ha trovato"
    return (
        f"{text}; censurato altrove come {', '.join(v.predicted)} da {_by(v.sources)}"
    )


def _layout_leak(v: ValueOutcome) -> str:
    where = "censurato nel Markdown" if v.protected else "in chiaro anche nel Markdown"
    return (
        f'"{v.value}" ({v.category}): in chiaro a {_pages(v.layout_leak_pages)} del PDF; '
        f"nel layout: {_layout(v)}; {where}"
    )


def _layout_leaks(d: DocScore) -> str | int:
    if d.layout_checked:
        return len(d.layout_leaked)
    return "non prodotto" if d.layout_missing else "–"


def _lost(v: ValueOutcome) -> str:
    return f'"{v.value}" ({v.category}): nel PDF a {_pages(v.pages)}; nel layout: {_layout(v)}'


def _value_lines(
    run: ProfileRun,
    keep: Callable[[ValueOutcome], bool],
    line: Callable[[ValueOutcome], str],
) -> list[str]:
    """One bullet per document, one sub-bullet per value."""
    out = []
    for d in run.docs:
        values = [v for v in d.values if keep(v)]
        if values:
            out.append(f"- `{d.id}`")
            out += [f"  - {line(v)}" for v in values]
    return out


def _hit_lines(
    run: ProfileRun, hits: Callable[[DocScore], list[Hit]], before: str
) -> list[str]:
    """One bullet per document, one sub-bullet per redaction, with page and detectors."""
    out = []
    for d in run.docs:
        if found := hits(d):
            out.append(f"- `{d.id}`")
            out += [
                f'  - "{h.text}" {before}{h.category}: {_pages([h.page])}, '
                f"trovato da {_by(h.sources) or 'n.d.'}"
                for h in found
            ]
    return out


def _details(run: ProfileRun) -> list[str]:
    out = ["### Dettaglio documenti", ""]
    rows = []
    for d in run.docs:
        expected = [v for v in d.values if v.expected and v.extracted]
        detected = sum(1 for v in expected if v.detected)
        rows.append(
            [
                d.id,
                STATUS.get(d.status, d.status),
                pct(detected / len(expected) if expected else None),
                len(d.leaked),
                _layout_leaks(d),
                len(d.false_positives),
                len(d.negatives_violated),
                d.residuals,
                num(d.duration_s),
            ]
        )
    out += [
        table(
            [
                "Documento",
                "Esito",
                "Recall",
                "Fughe",
                "Fughe PDF layout",
                "Falsi pos.",
                "Negativi",
                "Residui",
                "Tempo (s)",
            ],
            rows,
        ),
        "",
    ]
    leaks = _value_lines(
        run, lambda v: v.expected and v.extracted and not v.protected, _leak
    )
    out += ["### Valori rimasti in chiaro", "", *(leaks or ["Nessuno."]), ""]
    if any(d.layout_checked for d in run.docs):
        in_pdf = _value_lines(run, lambda v: v.layout_protected is False, _layout_leak)
        out += [
            "### Valori rimasti in chiaro nel PDF con layout",
            "",
            *(in_pdf or ["Nessuno."]),
            "",
        ]
    lost = _value_lines(run, lambda v: v.expected and not v.extracted, _lost)
    out += ["### Valori persi dall'estrazione", "", *(lost or ["Nessuno."]), ""]
    fps = _hit_lines(run, lambda d: d.false_positives, "")
    out += ["### Falsi positivi", "", *(fps or ["Nessuno."]), ""]
    negatives = _hit_lines(run, lambda d: d.negatives_violated, "come ")
    out += ["### Negativi noti censurati", "", *(negatives or ["Nessuno."]), ""]
    failed = [
        f"- `{d.id}`: {STATUS.get(d.status, d.status)}"
        + (f" - {d.error}" if d.error else "")
        for d in run.docs
        if d.status != "completed"
    ]
    out += ["### Documenti respinti o in errore", "", *(failed or ["Nessuno."]), ""]
    return out


def render_report(
    meta: dict[str, Any], runs: list[ProfileRun], thresholds: Thresholds
) -> str:
    """The whole report: context, summary, comparison, one section per profile."""
    context = [[k, v] for k, v in meta.items()]
    out = [
        "# Validazione dell'anonimizzazione",
        "",
        "Dataset sintetico: nomi, identificativi e strutture sono fittizi, quindi i valori sono "
        "mostrati in chiaro.",
        "",
        "## Contesto",
        "",
        table(["", ""], context),
        "",
        "## Sintesi",
        "",
        "- **Recall**: valori rilevati / valori attesi.",
        "- **Tasso di fuga**: valori rimasti in chiaro nel Markdown censurato / valori attesi.",
        "- **Precision**: occorrenze censurate che corrispondono a un valore atteso / occorrenze "
        "censurate.",
        "- Documenti: completati/respinti/errori. Tempi in secondi (durata del job).",
        "",
        table(
            [
                "Profilo",
                "Esito",
                "Documenti",
                "Recall",
                "Tasso fuga",
                "Fuga PDF layout",
                "Precision",
                "F1",
                "Doc. con fughe",
                "Negativi",
                "Tempo medio",
                "Tempo p95",
            ],
            _summary_rows(runs),
        ),
        "",
    ]
    if len(runs) > 1:
        out += ["## Confronto tra profili", "", _comparison(runs), ""]
    for run in runs:
        out += [_profile(run, thresholds), ""]
    return "\n".join(out).rstrip() + "\n"
