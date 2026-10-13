"""Validation run: every profile of the configuration on every document of the dataset.

Usage (``just validation``, ``validation/validate.sh``)::

    uv run python -m anonval.run [--config FILE] [--profilo NOME ...] [--tipo T ...]
        [--difficolta D ...] [--documento ID ...] [--url URL] [--concorrenza N]
        [--riprendi DIR] [--output DIR]

Writes ``<output>/<YYYYmmdd-HHMMSS>/`` (see ``engine``): ``report.md``, ``metriche.json``,
``config.yaml``, ``server.json`` and ``raw/<profilo>/<id>.json`` (reused by ``--riprendi``).
Exit code: 0 every profile passed its thresholds, 1 at least one failed, 2 the run could not start.
"""

import argparse
import asyncio
import datetime
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from anonval.engine import StartError, execute, load_dataset, preflight, service_client
from anonval.report import STATUS, ProfileRun, num, pct
from anonval.scoring import DocScore
from anonval.settings import Config, Profile, load_config

VALIDATION = Path(__file__).resolve().parents[1]
console = Console()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validazione dell'anonimizzazione sul dataset sintetico."
    )
    parser.add_argument("--config", type=Path, default=VALIDATION / "config.yaml")
    parser.add_argument("--profilo", action="append", default=[], help="ripetibile")
    parser.add_argument("--tipo", action="append", default=[], help="ripetibile")
    parser.add_argument("--difficolta", action="append", default=[], help="ripetibile")
    parser.add_argument("--documento", action="append", default=[], help="ripetibile")
    parser.add_argument("--url", help="URL del servizio (sovrascrive server.base_url)")
    parser.add_argument("--concorrenza", type=int, help="job inviati insieme")
    parser.add_argument(
        "--riprendi", type=Path, help="cartella di una run da completare"
    )
    parser.add_argument("--output", type=Path, default=VALIDATION / "results")
    return parser.parse_args(argv)


def configure(args: argparse.Namespace) -> tuple[Config, list[Profile]]:
    """Configuration with the command line overrides, and the profiles to run."""
    try:
        config = load_config(args.config)
        data = config.model_dump()
        if args.url:
            data["server"]["base_url"] = args.url
        if args.concorrenza:
            data["server"]["concorrenza"] = args.concorrenza
        for key, values in (
            ("tipi", args.tipo),
            ("difficolta", args.difficolta),
            ("documenti", args.documento),
        ):
            if values:
                data["dataset"][key] = values
        config = Config.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise StartError(f"Configurazione non valida ({args.config}):\n{exc}") from exc
    profiles = config.profili
    if args.profilo:
        known = {p.nome: p for p in profiles}
        if unknown := [n for n in args.profilo if n not in known]:
            raise StartError(
                f"Profili sconosciuti: {', '.join(unknown)} (disponibili: {', '.join(known)})"
            )
        profiles = [known[n] for n in args.profilo]
    return config, profiles


def _doc_line(profile: str, score: DocScore) -> str:
    color = {"completed": "green", "rejected": "yellow"}.get(score.status, "red")
    label = STATUS.get(score.status, score.status)
    head = f"[{color}]{label:<9}[/{color}] {profile} {score.id}"
    if score.status != "completed":
        return f"{head} {score.error or ''}"
    expected = [v for v in score.values if v.expected and v.extracted]
    recall = (
        sum(1 for v in expected if v.detected) / len(expected) if expected else None
    )
    leaks = len(score.leaked)
    leak_text = f"[red]fughe {leaks}[/red]" if leaks else "fughe 0"
    return (
        f"{head} recall {pct(recall)} {leak_text} falsi pos. {len(score.false_positives)} "
        f"{num(score.duration_s)} s"
    )


class RichObserver:
    """Terminal progress: overall and profile bars, one spinner per document in progress, one
    line per finished document."""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress
        self.overall: TaskID | None = None
        self.profile_task: TaskID | None = None
        self.documents = 0
        self.tasks: dict[str, tuple[TaskID, float]] = {}

    def start(self, profiles: list[str], documents: int) -> None:
        self.documents = documents
        self.overall = self.progress.add_task("Totale", total=len(profiles) * documents)

    def profile_started(self, profile: str) -> None:
        self.profile_task = self.progress.add_task(
            f"[bold]{profile}[/bold]", total=self.documents
        )

    def document_status(
        self, profile: str, doc_id: str, status: dict[str, Any]
    ) -> None:
        if doc_id not in self.tasks:
            task = self.progress.add_task(f"  {doc_id}", total=None)
            self.tasks[doc_id] = (task, time.monotonic())
        task, started = self.tasks[doc_id]
        parts = [f"{profile} {doc_id}", status.get("status", "")]
        if status.get("phase"):
            parts.append(str(status["phase"]))
        if status.get("position"):
            parts.append(f"coda n. {status['position']}")
        parts.append(f"{time.monotonic() - started:.0f} s")
        self.progress.update(task, description="  " + " | ".join(parts))

    def document_done(self, profile: str, score: DocScore) -> None:
        if score.id in self.tasks:
            self.progress.remove_task(self.tasks.pop(score.id)[0])
        self.progress.console.print(_doc_line(profile, score))
        for task in (self.profile_task, self.overall):
            if task is not None:
                self.progress.advance(task)

    def profile_done(self, run: ProfileRun) -> None:
        if self.profile_task is not None:
            self.progress.update(self.profile_task, visible=False)
        if run.aborted:
            self.progress.console.print(
                f"[red]Profilo {run.name} interrotto:[/red] {run.aborted}"
            )


def _summary_table(runs: list[ProfileRun]) -> Table:
    out = Table(title="Esito della validazione")
    for column in (
        "Profilo",
        "Esito",
        "Recall",
        "Fughe",
        "Precision",
        "F1",
        "Doc. con fughe",
    ):
        out.add_column(column)
    for run in runs:
        o = run.summary["overall"]
        outcome = {True: "[green]PASS[/green]", False: "[red]FAIL[/red]", None: "n.d."}
        out.add_row(
            run.name,
            outcome[run.passed],
            pct(o["recall"]),
            pct(o["tasso_fuga"]),
            pct(o["precision"]),
            pct(o["f1"]),
            str(o["documenti_con_fughe"]),
        )
    return out


async def main_async(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    try:
        config, profiles = configure(args)
        dataset = load_dataset(config)
    except StartError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    key = os.environ.get(config.server.chiave_env) or None
    async with service_client(config, transport) as client:
        try:
            server = await preflight(client, config, profiles, key)
        except StartError as exc:
            console.print(f"[red]{exc}[/red]")
            return 2
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = args.riprendi or args.output / stamp
        version = server["version"]
        console.print(
            f"Servizio [bold]{version.get('name', '')} {version.get('version', '')}[/bold] su "
            f"{config.server.base_url}: {len(dataset)} documenti x {len(profiles)} profili "
            f"(concorrenza {config.server.concorrenza}). Output: {run_dir}"
        )
        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        with progress:
            runs = await execute(
                client,
                config,
                profiles,
                dataset,
                server,
                run_dir,
                key,
                RichObserver(progress),
            )
    console.print(_summary_table(runs))
    console.print(f"Report: [bold]{run_dir / 'report.md'}[/bold]")
    return 0 if all(r.passed is not False for r in runs) else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
