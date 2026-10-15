"""Markdown report of a validation run, in Italian with it-IT numbers.

The values listed are those of the synthetic dataset: no real personal data can appear here.
"""

from dataclasses import dataclass

from anonval.metrics import Counts, Summary, verdict
from anonval.scoring import DocScore
from anonval.settings import Config, Profile


@dataclass
class ProfileRun:
    profile: Profile
    scores: list[DocScore]
    summary: Summary


def num(value: float | None, digits: int = 0) -> str:
    """it-IT number: comma for decimals, dot for thousands; ``-`` for None."""
    if value is None:
        return "-"
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pct(value: float | None) -> str:
    return "-" if value is None else f"{num(value * 100, 1)}%"


def _row(name: str, c: Counts) -> str:
    return (
        f"| {name} | {c.expected} | {c.detected} | {c.leaked} | "
        f"{pct(c.detected / c.expected if c.expected else None)} | "
        f"{pct(c.leaked / c.expected if c.expected else None)} |"
    )


def _table(title: str, groups: dict[str, Counts]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| | Attesi | Rilevati | Sfuggiti | Recall | Fuga |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lines += [_row(name, c) for name, c in sorted(groups.items())]
    return [*lines, ""]


def render_report(runs: list[ProfileRun], config: Config) -> str:
    """The whole report: summary of every profile, then the details."""
    t = config.soglie
    lines = [
        "# Validazione dell'anonimizzazione",
        "",
        f"Soglie: recall almeno {pct(t.recall_min)}, tasso di fuga al massimo "
        f"{pct(t.tasso_fuga_max)}, precisione almeno {pct(t.precision_min)}.",
        "",
        "| Profilo | Esito | Documenti | Rilasciati | Bloccati | Recall | Fuga PDF "
        "| Fuga Markdown | Precisione |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        s = run.summary
        failed = verdict(s, t)
        lines.append(
            f"| {run.profile.nome} | {'FAIL' if failed else 'PASS'} | {s.documents} | "
            f"{s.released} | {sum(s.blocked.values())} | {pct(s.recall)} | "
            f"{pct(s.leak_rate)} | {pct(s.markdown_leak_rate)} | {pct(s.precision)} |"
        )
    lines.append("")
    for run in runs:
        s = run.summary
        lines += [f"## Profilo {run.profile.nome}", ""]
        if run.profile.descrizione:
            lines += [run.profile.descrizione, ""]
        if failed := verdict(s, t):
            lines += ["Soglie non rispettate: " + "; ".join(failed) + ".", ""]
        lines += [
            f"- Valori attesi: {s.values.expected}, rilevati {s.values.detected}, "
            f"sfuggiti {s.values.leaked}; non localizzati nel PDF originale: {s.not_located}.",
            f"- Riquadri: {s.boxes} (corretti {s.correct_boxes}, su categorie disattivate "
            f"{s.disabled_boxes}, falsi positivi {s.false_positives}).",
            f"- Negativi violati: {s.negatives_violated}; valori nel testo dei PDF "
            f"rilasciati: {s.text_leaks}.",
            f"- Markdown: {s.markdown_documents} documenti, {s.markdown_leaked} valori ancora "
            f"leggibili su {s.markdown_expected} attesi.",
            f"- Bloccati: {dict(s.blocked) or 0}; non elaborati: {s.failed}.",
            "",
        ]
        lines += _table("Per categoria", s.by_category)
        lines += _table("Per difficoltà", s.by_difficulty)
        lines += _table("Per tipo di documento", s.by_type)
        leaks = [(doc.id, v) for doc in run.scores for v in doc.values if v.leaked]
        if leaks:
            lines += [
                "### Valori sfuggiti",
                "",
                "| Documento | Categoria | Valore | Pagine | Copertura migliore |",
                "|---|---|---|---|---:|",
            ]
            lines += [
                f"| {doc} | {v.category} | {v.value} | {', '.join(map(str, v.pages))} | "
                f"{pct(v.coverage)} |"
                for doc, v in leaks
            ]
            lines.append("")
        fps = [(doc.id, h) for doc in run.scores for h in doc.false_positives]
        if fps:
            lines += [
                "### Falsi positivi",
                "",
                "| Documento | Pagina | Categoria |",
                "|---|---:|---|",
            ]
            lines += [f"| {doc} | {h.page} | {h.category} |" for doc, h in fps]
            lines.append("")
        negatives = [(doc.id, h) for doc in run.scores for h in doc.negatives_violated]
        if negatives:
            lines += [
                "### Negativi violati",
                "",
                "| Documento | Pagina | Valore | Categoria |",
            ]
            lines += ["|---|---:|---|---|"]
            lines += [
                f"| {doc} | {h.page} | {h.value} | {h.category} |"
                for doc, h in negatives
            ]
            lines.append("")
        problems = [doc for doc in run.scores if not doc.released]
        if problems:
            lines += [
                "### Documenti non rilasciati",
                "",
                "| Documento | Stato | Motivo |",
            ]
            lines += ["|---|---|---|"]
            lines += [
                f"| {doc.id} | {doc.status} | {doc.reason or doc.error or ''} |"
                for doc in problems
            ]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
