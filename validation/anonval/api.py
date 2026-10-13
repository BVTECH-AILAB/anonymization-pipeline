"""Web API of the validation, used by the "Validazione" section of the web UI.

A local development tool, never deployed: it runs the same engine as ``just validation`` against
the anonymization service and writes the same run folders in ``results/``. One run at a time: the
GPU of the service is the bottleneck.

Run it with ``just validation-api`` (host) or the ``validation`` service of docker compose.
"""

import asyncio
import datetime
import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from anonval.engine import (
    CANCELLED,
    RUNNING,
    Dataset,
    ServiceDown,
    StartError,
    execute,
    load_dataset,
    preflight,
    read_state,
    service_client,
    write_state,
)
from anonval.report import ProfileRun
from anonval.scoring import DocScore
from anonval.settings import Config, load_config

logger = logging.getLogger(__name__)

VALIDATION = Path(__file__).resolve().parents[1]
RUN_ID = r"^\d{8}-\d{6}(?:-\d{1,3})?$"
INTERRUPTED, UNKNOWN = "interrotta", "sconosciuto"
FINISHED_KEPT = 500  # finished documents kept in the live state


class ApiSettings(BaseSettings):
    """Settings of the validation API (prefix ``ANONVAL_``)."""

    model_config = SettingsConfigDict(env_prefix="ANONVAL_", extra="ignore")

    api_url: str = (
        ""  # anonymization service; empty: server.base_url of the configuration
    )
    key: str = ""  # credential: key of the pseudonym map, never written to disk
    config_file: str = str(VALIDATION / "config.yaml")
    results_dir: str = str(VALIDATION / "results")


class RunRequest(BaseModel):
    config: Config
    key: str | None = Field(default=None, max_length=200)


class ResumeRequest(BaseModel):
    key: str | None = Field(default=None, max_length=200)


@dataclass
class LiveRun:
    """State of the run in progress, read by ``GET /runs/{id}`` (an engine ``Observer``)."""

    id: str
    task: asyncio.Task[None] | None = None
    profiles: list[str] = field(default_factory=list)
    documents: int = 0
    completed: int = 0
    profile: str | None = None
    per_profile: dict[str, int] = field(default_factory=dict)
    running: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=FINISHED_KEPT)
    )
    started: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    error: str | None = None

    def start(self, profiles: list[str], documents: int) -> None:
        self.profiles, self.documents = profiles, documents

    def profile_started(self, profile: str) -> None:
        self.profile = profile
        self.per_profile[profile] = 0

    def document_status(
        self, profile: str, doc_id: str, status: dict[str, Any]
    ) -> None:
        entry = self.running.setdefault(
            doc_id, {"profile": profile, "document": doc_id, "since": time.monotonic()}
        )
        entry.update(
            status=status.get("status"),
            phase=status.get("phase"),
            position=status.get("position"),
        )

    def document_done(self, profile: str, score: DocScore) -> None:
        self.running.pop(score.id, None)
        self.completed += 1
        self.per_profile[profile] = self.per_profile.get(profile, 0) + 1
        expected = [v for v in score.values if v.expected and v.extracted]
        self.finished.append(
            {
                "profile": profile,
                "document": score.id,
                "status": score.status,
                "recall": sum(1 for v in expected if v.detected) / len(expected)
                if expected
                else None,
                "leaks": len(score.leaked),
                "false_positives": len(score.false_positives),
                "seconds": score.duration_s,
                "error": score.error,
            }
        )

    def profile_done(self, run: ProfileRun) -> None:
        if run.aborted:
            self.error = run.aborted

    def view(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "profiles": self.profiles,
            "profile": self.profile,
            "documents": self.documents,
            "total": len(self.profiles) * self.documents,
            "completed": self.completed,
            "per_profile": self.per_profile,
            "running": [
                {
                    **{k: v for k, v in r.items() if k != "since"},
                    "seconds": round(now - r["since"]),
                }
                for r in self.running.values()
            ],
            "finished": list(self.finished),
            "started": self.started,
            "error": self.error,
        }


def _settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def _results(request: Request) -> Path:
    return Path(_settings(request).results_dir)


def _run_dir(request: Request, run_id: str) -> Path:
    """Folder of an existing run; the id pattern keeps paths inside the results folder."""
    path = _results(request) / run_id
    if not re.match(RUN_ID, run_id) or not path.is_dir():
        raise HTTPException(404, "Run inesistente.")
    return path


def _live(request: Request) -> LiveRun | None:
    live: LiveRun | None = request.app.state.live
    if live and live.task and not live.task.done():
        return live
    return None


def _state(request: Request, run_dir: Path) -> str:
    live = request.app.state.live
    if live and live.id == run_dir.name:
        if live.task and not live.task.done():
            return RUNNING
        if live.error and read_state(run_dir) == RUNNING:
            return INTERRUPTED
    state = read_state(run_dir)
    if state == RUNNING:
        return INTERRUPTED  # left running by a process that stopped
    return state or UNKNOWN


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _outcomes(run_dir: Path) -> list[dict[str, Any]]:
    metrics = _read_json(run_dir / "metriche.json") or {}
    out = []
    for profile in metrics.get("profili", []):
        overall = profile["metriche"]["overall"]
        out.append(
            {
                "nome": profile["nome"],
                "esito": profile["esito"],
                "interrotto": profile["interrotto"],
                "soglie": profile["soglie"],
                "recall": overall["recall"],
                "tasso_fuga": overall["tasso_fuga"],
                "precision": overall["precision"],
                "f1": overall["f1"],
                "documenti": overall["documenti"],
                "documenti_con_fughe": overall["documenti_con_fughe"],
                "negativi_violati": overall["negativi_violati"],
                "tempi_s": overall["tempi_s"],
                "per_categoria": {
                    k: {
                        "attesi": v["attesi"],
                        "fughe": v["fughe"],
                        "tasso_fuga": v["tasso_fuga"],
                    }
                    for k, v in profile["metriche"]["by_category"].items()
                },
            }
        )
    return out


def _prepare(request: Request, config: Config) -> tuple[Config, Dataset]:
    """The configuration with the service URL and dataset of this API, and its documents."""
    settings = _settings(request)
    data = config.model_dump()
    data["dataset"]["percorso"] = request.app.state.default.dataset.percorso
    if settings.api_url:
        data["server"]["base_url"] = settings.api_url
    config = Config.model_validate(data)
    try:
        return config, load_dataset(config)
    except StartError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _start(
    request: Request, run_id: str, config: Config, dataset: Dataset, key: str | None
) -> None:
    """Preflight now (errors reach the caller), then the run in the background."""
    if _live(request):
        raise HTTPException(409, "Una validazione e' gia' in corso.")
    transport = request.app.state.transport
    key = key or _settings(request).key or None
    profiles = config.profili
    try:
        async with service_client(config, transport) as client:
            server = await preflight(client, config, profiles, key)
    except ServiceDown as exc:
        raise HTTPException(503, str(exc)) from exc
    except StartError as exc:
        raise HTTPException(422, str(exc)) from exc
    live = LiveRun(run_id)
    run_dir = _results(request) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_state(run_dir, RUNNING)

    async def work() -> None:
        try:
            async with service_client(config, transport) as client:
                await execute(
                    client, config, profiles, dataset, server, run_dir, key, live
                )
        except asyncio.CancelledError:
            logger.info("Validation %s cancelled", run_id)
        except Exception as exc:  # the run stops, the API keeps serving
            logger.exception("Validation %s failed", run_id)
            live.error = f"{type(exc).__name__}: {exc}"

    request.app.state.live = live
    live.task = asyncio.create_task(work())


def create_app(
    settings: ApiSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """The validation API.

    Args:
        settings: Defaults to the ``ANONVAL_*`` environment.
        transport: HTTP transport towards the anonymization service (tests).
    """
    settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        live = app.state.live
        if live and live.task and not live.task.done():
            live.task.cancel()

    app = FastAPI(title="anonval", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.transport = transport
    app.state.live = None
    app.state.default = load_config(Path(settings.config_file))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config")
    async def default_config(request: Request) -> dict[str, Any]:
        config: Config = request.app.state.default
        manifest = _read_json(Path(config.dataset.percorso) / "manifest.json") or {}
        return {
            "config": config.model_dump(mode="json"),
            "documents": manifest.get("documents", []),
            "key_available": bool(settings.key),
        }

    @app.post("/runs", status_code=202)
    async def new_run(body: RunRequest, request: Request) -> dict[str, str]:
        config, dataset = _prepare(request, body.config)
        base = run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        n = 1
        while (_results(request) / run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        await _start(request, run_id, config, dataset, body.key)
        return {"id": run_id}

    @app.get("/runs")
    async def runs(request: Request) -> list[dict[str, Any]]:
        root = _results(request)
        if not root.is_dir():
            return []
        out = []
        for run_dir in sorted(root.iterdir(), reverse=True):
            if not (run_dir.is_dir() and re.match(RUN_ID, run_dir.name)):
                continue
            config = (
                yaml.safe_load((run_dir / "config.yaml").read_text())
                if (run_dir / "config.yaml").exists()
                else {}
            )
            out.append(
                {
                    "id": run_dir.name,
                    "stato": _state(request, run_dir),
                    "profili": [
                        p.get("nome") for p in (config or {}).get("profili", [])
                    ],
                    "dataset": (config or {}).get("dataset", {}),
                    "esiti": {o["nome"]: o["esito"] for o in _outcomes(run_dir)},
                }
            )
        return out

    @app.get("/runs/{run_id}")
    async def run(run_id: str, request: Request) -> dict[str, Any]:
        run_dir = _run_dir(request, run_id)
        live = request.app.state.live
        return {
            "id": run_id,
            "stato": _state(request, run_dir),
            "live": live.view() if live and live.id == run_id else None,
            "profili": _outcomes(run_dir),
        }

    @app.get("/runs/{run_id}/report", response_class=PlainTextResponse)
    async def report(run_id: str, request: Request) -> str:
        path = _run_dir(request, run_id) / "report.md"
        if not path.exists():
            raise HTTPException(404, "Report non ancora disponibile.")
        return path.read_text(encoding="utf-8")

    @app.get("/runs/{run_id}/metrics")
    async def metrics(run_id: str, request: Request) -> Any:
        data = _read_json(_run_dir(request, run_id) / "metriche.json")
        if data is None:
            raise HTTPException(404, "Metriche non ancora disponibili.")
        return data

    @app.get("/runs/{run_id}/config")
    async def run_config(run_id: str, request: Request) -> Any:
        path = _run_dir(request, run_id) / "config.yaml"
        if not path.exists():
            raise HTTPException(404, "Configurazione non disponibile per questa run.")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @app.post("/runs/{run_id}/cancel", status_code=202)
    async def cancel(run_id: str, request: Request) -> dict[str, str]:
        _run_dir(request, run_id)
        live = _live(request)
        if not live or live.id != run_id or not live.task:
            raise HTTPException(409, "La run non e' in corso.")
        live.task.cancel()
        return {"id": run_id}

    @app.post("/runs/{run_id}/resume", status_code=202)
    async def resume(
        run_id: str, body: ResumeRequest, request: Request
    ) -> dict[str, str]:
        run_dir = _run_dir(request, run_id)
        if _state(request, run_dir) not in (INTERRUPTED, CANCELLED):
            raise HTTPException(409, "Si riprendono solo run interrotte o annullate.")
        path = run_dir / "config.yaml"
        if not path.exists():
            raise HTTPException(409, "Configurazione della run non disponibile.")
        config, dataset = _prepare(request, load_config(path))
        await _start(request, run_id, config, dataset, body.key)
        return {"id": run_id}

    return app


def app() -> FastAPI:
    """Factory for uvicorn (``uvicorn anonval.api:app --factory``)."""
    return create_app()
