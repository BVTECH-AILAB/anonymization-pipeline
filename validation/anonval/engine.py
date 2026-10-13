"""Validation engine shared by the command line (``run.py``) and the web API (``api.py``).

``execute`` runs every profile on every document, reports progress to an ``Observer`` and writes
the run folder:

- ``config.yaml``: the configuration used (never the key);
- ``server.json``: version, categories, models and configuration of the service;
- ``raw/<profilo>/<id>.json``: the result of each document, reused when a run is resumed;
- ``report.md`` and ``metriche.json``;
- ``stato.json``: ``in_corso``, ``completata`` or ``annullata``. A run folder left ``in_corso`` by a
  process that stopped is an interrupted run.

A cancelled ``execute`` (``asyncio.CancelledError``) still writes a partial report.
"""

import asyncio
import dataclasses
import datetime
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from anonval.client import ApiError, ServiceClient, run_job
from anonval.report import ProfileRun, render_report
from anonval.scoring import (
    DocScore,
    active_categories,
    error_score,
    score_document,
    summarize,
    verdict,
)
from anonval.settings import Config, Parameters, Profile

Dataset = list[tuple[dict[str, Any], Path]]
RUNNING, COMPLETED, CANCELLED = "in_corso", "completata", "annullata"
CANCELLED_NOTE = "run annullata"


class StartError(Exception):
    """The run cannot start: configuration, dataset or server not usable."""


class Observer(Protocol):
    """Progress of a run: the terminal display or the state read by the web API."""

    def start(self, profiles: list[str], documents: int) -> None: ...

    def profile_started(self, profile: str) -> None: ...

    def document_status(
        self, profile: str, doc_id: str, status: dict[str, Any]
    ) -> None: ...

    def document_done(self, profile: str, score: DocScore) -> None: ...

    def profile_done(self, run: ProfileRun) -> None: ...


def load_dataset(config: Config) -> Dataset:
    """Ground truth and PDF path of every document selected by the filters.

    Raises:
        StartError: No manifest, or no document matches the filters.
    """
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


@asynccontextmanager
async def service_client(
    config: Config, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[ServiceClient]:
    """Client of the anonymization service of the configuration."""
    timeout = httpx.Timeout(120, connect=10)
    async with httpx.AsyncClient(
        base_url=config.server.base_url, timeout=timeout, transport=transport
    ) as http:
        yield ServiceClient(http)


async def preflight(
    client: ServiceClient, config: Config, profiles: list[Profile], key: str | None
) -> dict[str, Any]:
    """Service reachable, models of every profile available, key when needed.

    Raises:
        StartError: With the reason, in Italian.
    """
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
                f"{profile.nome}: modalita' pseudonym senza chiave: imposta "
                f"{config.server.chiave_env} oppure usa mode: tag"
            )
    if problems:
        raise StartError("Profili non eseguibili:\n  - " + "\n  - ".join(problems))
    return server


class _Engine:
    def __init__(
        self,
        client: ServiceClient,
        config: Config,
        dataset: Dataset,
        server: dict[str, Any],
        run_dir: Path,
        key: str | None,
        observer: Observer,
    ) -> None:
        self.client, self.config, self.dataset = client, config, dataset
        self.server, self.run_dir, self.key = server, run_dir, key
        self.observer = observer
        self.done: list[ProfileRun] = []
        self.current: tuple[Profile, Parameters, dict[str, DocScore]] | None = None

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
            self.observer.document_status(profile.nome, gold["id"], {"status": "invio"})
            try:
                job = await run_job(
                    self.client,
                    pdf.name,
                    pdf.read_bytes(),
                    params.form(),
                    self.key,
                    self.config.server.poll_interval_s,
                    self.config.server.timeout_documento_s,
                    lambda status: self.observer.document_status(
                        profile.nome, gold["id"], status
                    ),
                )
            except httpx.HTTPError as exc:
                job = {"status": "error", "error": f"rete: {type(exc).__name__}"}
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        if job["status"] == "completed":
            return score_document(gold, job, active)
        error = job.get("error") or job.get("reason")
        if job.get("residuals"):
            categories = sorted({r.get("category", "") for r in job["residuals"]})
            error = f"{error}: residui {', '.join(categories)}"
        return error_score(gold, job["status"], error)

    def profile_run(
        self,
        profile: Profile,
        params: Parameters,
        scores: dict[str, DocScore],
        aborted: str | None,
    ) -> ProfileRun:
        docs = [scores[gold["id"]] for gold, _ in self.dataset if gold["id"] in scores]
        summary = summarize(docs)
        return ProfileRun(
            profile.nome,
            profile.descrizione,
            params.form(),
            summary,
            verdict(summary["overall"], self.config.soglie),
            docs,
            aborted,
        )

    async def profile(self, profile: Profile) -> ProfileRun:
        params = self.config.effective(profile)
        active = active_categories(
            self.server["categories"]["active"], params.enable, params.disable
        )
        self.observer.profile_started(profile.nome)
        limit = asyncio.Semaphore(self.config.server.concorrenza)
        aborted: list[str] = []
        scores: dict[str, DocScore] = {}
        self.current = (profile, params, scores)

        async def one(gold: dict[str, Any], pdf: Path) -> None:
            async with limit:
                if aborted:
                    score = error_score(gold, "error", "profilo interrotto")
                else:
                    try:
                        score = await self.document(profile, params, active, gold, pdf)
                    except ApiError as exc:
                        aborted.append(str(exc))
                        score = error_score(gold, "error", str(exc))
                scores[gold["id"]] = score
                self.observer.document_done(profile.nome, score)

        await asyncio.gather(*(one(gold, pdf) for gold, pdf in self.dataset))
        self.current = None
        run = self.profile_run(profile, params, scores, aborted[0] if aborted else None)
        self.observer.profile_done(run)
        return run

    def partial(self) -> list[ProfileRun]:
        """Finished profiles and the one in progress, for the report of a cancelled run."""
        runs = list(self.done)
        if self.current:
            profile, params, scores = self.current
            runs.append(self.profile_run(profile, params, scores, CANCELLED_NOTE))
        return runs


def write_state(run_dir: Path, state: str) -> None:
    (run_dir / "stato.json").write_text(
        json.dumps({"stato": state}, ensure_ascii=False), encoding="utf-8"
    )


def read_state(run_dir: Path) -> str | None:
    path = run_dir / "stato.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("stato")


def write_config(run_dir: Path, config: Config) -> None:
    """Snapshot of the configuration of the run (``config.yaml`` schema)."""
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump(
            config.model_dump(mode="json"), allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )


def _meta(
    config: Config,
    profiles: list[Profile],
    documents: int,
    server: dict[str, Any],
    seconds: float,
    state: str,
) -> dict[str, Any]:
    version = server["version"]
    phases = server["configuration"].get("phases", {})
    defaults = server["models"].get("defaults", {})
    filters = config.dataset
    return {
        "Data": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Stato": state,
        "Servizio": f"{version.get('name', '')} {version.get('version', '')}",
        "URL": config.server.base_url,
        "Default del server": (
            f"mode={phases.get('redaction', {}).get('mode')}, "
            f"verification={phases.get('verification', {}).get('mode')}, "
            f"extraction={phases.get('extraction', {}).get('native_pdf', {}).get('engine')}"
        ),
        "Modelli di default": ", ".join(f"{k}={v}" for k, v in defaults.items()),
        "Documenti": documents,
        "Filtri": (
            f"tipi={','.join(filters.tipi) or 'tutti'}; "
            f"difficolta={','.join(filters.difficolta) or 'tutte'}; "
            f"documenti={','.join(filters.documenti) or 'tutti'}"
        ),
        "Profili": ", ".join(p.nome for p in profiles),
        "Durata della run": f"{seconds / 60:.1f} min".replace(".", ","),
    }


def write_results(
    run_dir: Path, config: Config, meta: dict[str, Any], runs: list[ProfileRun]
) -> None:
    """``report.md`` and ``metriche.json`` of the run."""
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


async def execute(
    client: ServiceClient,
    config: Config,
    profiles: list[Profile],
    dataset: Dataset,
    server: dict[str, Any],
    run_dir: Path,
    key: str | None,
    observer: Observer,
) -> list[ProfileRun]:
    """Run the profiles and write the run folder (``server`` comes from ``preflight``).

    Raises:
        asyncio.CancelledError: The run was cancelled; the partial report is written first.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    write_config(run_dir, config)
    write_state(run_dir, RUNNING)
    (run_dir / "server.json").write_text(
        json.dumps(server, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    engine = _Engine(client, config, dataset, server, run_dir, key, observer)
    observer.start([p.nome for p in profiles], len(dataset))
    started = time.monotonic()
    try:
        for profile in profiles:
            engine.done.append(await engine.profile(profile))
    except asyncio.CancelledError:
        meta = _meta(
            config,
            profiles,
            len(dataset),
            server,
            time.monotonic() - started,
            CANCELLED,
        )
        write_results(run_dir, config, meta, engine.partial())
        write_state(run_dir, CANCELLED)
        raise
    meta = _meta(
        config, profiles, len(dataset), server, time.monotonic() - started, COMPLETED
    )
    write_results(run_dir, config, meta, engine.done)
    write_state(run_dir, COMPLETED)
    return engine.done
