"""Validation run: every profile of the configuration on every document of the dataset.

Usage (``just validation``, ``validation/validate.sh``)::

    uv run python -m anonval.run [--config FILE] [--profilo NOME ...] [--tipo T ...]
        [--difficolta D ...] [--documento ID ...] [--url URL] [--concorrenza N]
        [--riprendi DIR] [--output DIR]

Writes ``<output>/<YYYYmmdd-HHMMSS>/``: ``report.md``, ``metriche.json``, ``server.json`` and
``raw/<profilo>/<id>.json`` (the result of each document, reused by ``--riprendi``).
Exit code: 0 every profile passed its thresholds, 1 at least one failed, 2 the run could not start.
"""

import argparse
import asyncio
import dataclasses
import datetime
import json
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

from anonval.client import ApiError, ServiceClient, run_job
from anonval.report import STATUS, ProfileRun, num, pct, render_report
from anonval.scoring import (
    DocScore,
    active_categories,
    error_score,
    score_document,
    summarize,
    verdict,
)
from anonval.settings import Config, Parameters, Profile, load_config

VALIDATION = Path(__file__).resolve().parents[1]
console = Console()


class StartError(Exception):
    """The run cannot start: configuration, dataset or server not usable."""


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


def load_dataset(config: Config) -> list[tuple[dict[str, Any], Path]]:
    """Ground truth and PDF path of every document selected by the filters."""
    root = Path(config.dataset.percorso)
    manifest = root / "manifest.json"
    if not manifest.exists():
        raise StartError(
            f"Manca {manifest}: genera il dataset con `just validation-dataset`."
        )
    documents = json.loads(manifest.read_text(encoding="utf-8"))["documents"]
    f = config.dataset
    selected = [
        d
        for d in documents
        if (not f.tipi or d["type"] in f.tipi)
        and (not f.difficolta or d["difficulty"] in f.difficolta)
        and (not f.documenti or d["id"] in f.documenti)
    ]
    if not selected:
        raise StartError("Nessun documento corrisponde ai filtri del dataset.")
    return [
        (
            json.loads((root / d["ground_truth"]).read_text(encoding="utf-8")),
            root / d["pdf"],
        )
        for d in selected
    ]


async def preflight(
    client: ServiceClient, config: Config, profiles: list[Profile], key: str | None
) -> dict[str, Any]:
    """Service reachable, models and categories of every profile valid, key when needed."""
    url = config.server.base_url
    try:
        await client.get("/healthz")
        server = {
            "version": await client.get("/version"),
            "categories": await client.get("/categories"),
            "models": await client.get("/models"),
            "configuration": await client.get("/configuration"),
        }
    except httpx.HTTPError as exc:
        raise StartError(
            f"Servizio non raggiungibile su {url} ({type(exc).__name__}): avvialo con `just up` "
            "e imposta ANONPIP_ALLOW_TRANSCRIPT=true."
        ) from exc
    except ApiError as exc:
        raise StartError(f"Il servizio su {url} risponde con errore: {exc}") from exc
    catalog = {m["name"]: m for m in server["models"]["models"]}
    phases = server["configuration"].get("phases", {})
    default_mode = phases.get("redaction", {}).get("mode")
    problems = []
    for profile in profiles:
        params = config.effective(profile)
        for model in params.models():
            if model not in catalog:
                problems.append(
                    f"{profile.nome}: modello {model} non nel catalogo (GET /models)"
                )
            elif catalog[model].get("present") is False:
                problems.append(
                    f"{profile.nome}: modello {model} non presente nel backend"
                )
        if (params.mode or default_mode) == "pseudonym" and not key:
            problems.append(
                f"{profile.nome}: modalita' pseudonym senza chiave: esporta "
                f"{config.server.chiave_env} oppure usa mode: tag"
            )
    if problems:
        raise StartError("Profili non eseguibili:\n  - " + "\n  - ".join(problems))
    return server


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


class Runner:
    """Runs the profiles with a live progress display."""

    def __init__(
        self,
        client: ServiceClient,
        config: Config,
        dataset: list[tuple[dict[str, Any], Path]],
        server: dict[str, Any],
        run_dir: Path,
        key: str | None,
    ) -> None:
        self.client, self.config, self.dataset = client, config, dataset
        self.server, self.run_dir, self.key = server, run_dir, key
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

    async def document(
        self,
        profile: Profile,
        params: Parameters,
        active: set[str],
        gold: dict[str, Any],
        pdf: Path,
    ) -> DocScore:
        raw_path = self.run_dir / "raw" / profile.nome / f"{gold['id']}.json"
        job: dict[str, Any]
        if raw_path.exists():
            job = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            task = self.progress.add_task(f"  {gold['id']}: in invio", total=None)
            started = time.monotonic()

            def on_status(status: dict[str, Any]) -> None:
                parts = [gold["id"], status.get("status", "")]
                if status.get("phase"):
                    parts.append(str(status["phase"]))
                if status.get("position"):
                    parts.append(f"coda n. {status['position']}")
                parts.append(f"{time.monotonic() - started:.0f} s")
                self.progress.update(task, description="  " + " | ".join(parts))

            try:
                job = await run_job(
                    self.client,
                    pdf.name,
                    pdf.read_bytes(),
                    params.form(),
                    self.key,
                    self.config.server.poll_interval_s,
                    self.config.server.timeout_documento_s,
                    on_status,
                )
            except httpx.HTTPError as exc:
                job = {"status": "error", "error": f"rete: {type(exc).__name__}"}
            finally:
                self.progress.remove_task(task)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        if job["status"] == "completed":
            return score_document(gold, job, active)
        error = job.get("error") or job.get("reason")
        if job.get("residuals"):
            categories = sorted({r.get("category", "") for r in job["residuals"]})
            error = f"{error}: residui {', '.join(categories)}"
        return error_score(gold, job["status"], error)

    async def profile(self, profile: Profile, overall: TaskID) -> ProfileRun:
        params = self.config.effective(profile)
        active = active_categories(
            self.server["categories"]["active"], params.enable, params.disable
        )
        task = self.progress.add_task(
            f"[bold]{profile.nome}[/bold]", total=len(self.dataset)
        )
        limit = asyncio.Semaphore(self.config.server.concorrenza)
        aborted: list[str] = []
        scores: dict[str, DocScore] = {}

        async def one(gold: dict[str, Any], pdf: Path) -> None:
            async with limit:
                if aborted:
                    scores[gold["id"]] = error_score(
                        gold, "error", "profilo interrotto"
                    )
                else:
                    try:
                        score = await self.document(profile, params, active, gold, pdf)
                    except ApiError as exc:
                        aborted.append(str(exc))
                        score = error_score(gold, "error", str(exc))
                    scores[gold["id"]] = score
                    self.progress.console.print(_doc_line(profile.nome, score))
            self.progress.advance(task)
            self.progress.advance(overall)

        await asyncio.gather(*(one(gold, pdf) for gold, pdf in self.dataset))
        self.progress.update(task, visible=False)
        docs = [scores[gold["id"]] for gold, _ in self.dataset]
        summary = summarize(docs)
        return ProfileRun(
            profile.nome,
            profile.descrizione,
            params.form(),
            summary,
            verdict(summary["overall"], self.config.soglie),
            docs,
            aborted[0] if aborted else None,
        )

    async def run(self, profiles: list[Profile]) -> list[ProfileRun]:
        runs = []
        with self.progress:
            overall = self.progress.add_task(
                "Totale", total=len(profiles) * len(self.dataset)
            )
            for profile in profiles:
                run = await self.profile(profile, overall)
                if run.aborted:
                    self.progress.console.print(
                        f"[red]Profilo {profile.nome} interrotto:[/red] {run.aborted}"
                    )
                runs.append(run)
        return runs


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
    timeout = httpx.Timeout(120, connect=10)
    async with httpx.AsyncClient(
        base_url=config.server.base_url, timeout=timeout, transport=transport
    ) as http:
        client = ServiceClient(http)
        try:
            server = await preflight(client, config, profiles, key)
        except StartError as exc:
            console.print(f"[red]{exc}[/red]")
            return 2
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = args.riprendi or args.output / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "server.json").write_text(
            json.dumps(server, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        version = server["version"]
        console.print(
            f"Servizio [bold]{version.get('name', '')} {version.get('version', '')}[/bold] su "
            f"{config.server.base_url}: {len(dataset)} documenti x {len(profiles)} profili "
            f"(concorrenza {config.server.concorrenza}). Output: {run_dir}"
        )
        started = time.monotonic()
        runs = await Runner(client, config, dataset, server, run_dir, key).run(profiles)
    phases = server["configuration"].get("phases", {})
    defaults = server["models"].get("defaults", {})
    filters = config.dataset
    meta = {
        "Data": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Servizio": f"{version.get('name', '')} {version.get('version', '')}",
        "URL": config.server.base_url,
        "Default del server": (
            f"mode={phases.get('redaction', {}).get('mode')}, "
            f"verification={phases.get('verification', {}).get('mode')}, "
            f"extraction={phases.get('extraction', {}).get('native_pdf', {}).get('engine')}"
        ),
        "Modelli di default": ", ".join(f"{k}={v}" for k, v in defaults.items()),
        "Documenti": len(dataset),
        "Filtri": (
            f"tipi={','.join(filters.tipi) or 'tutti'}; "
            f"difficolta={','.join(filters.difficolta) or 'tutte'}; "
            f"documenti={','.join(filters.documenti) or 'tutti'}"
        ),
        "Profili": ", ".join(p.nome for p in profiles),
        "Durata della run": f"{(time.monotonic() - started) / 60:.1f} min".replace(
            ".", ","
        ),
    }
    (run_dir / "report.md").write_text(
        render_report(meta, runs, config.soglie), encoding="utf-8"
    )
    metrics = {
        "meta": meta,
        "soglie": config.soglie.model_dump(),
        "profili": [
            {
                "nome": r.name,
                "parametri": r.form,
                "interrotto": r.aborted,
                "esito": r.passed,
                "soglie": r.verdict,
                "metriche": r.summary,
                "documenti": [dataclasses.asdict(d) for d in r.docs],
            }
            for r in runs
        ],
    }
    (run_dir / "metriche.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print(_summary_table(runs))
    console.print(f"Report: [bold]{run_dir / 'report.md'}[/bold]")
    return 0 if all(r.passed is not False for r in runs) else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
