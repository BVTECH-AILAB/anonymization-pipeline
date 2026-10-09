"""Anonymization service: request configuration, concurrency slots, error mapping, status."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request

from anonymization_pipeline import __version__
from anonymization_pipeline.config import Settings
from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import model_dir
from anonymization_pipeline.domain import Result
from anonymization_pipeline.errors import (
    BadRequestError,
    InvalidDocumentError,
    RestoreError,
    ServerBusyError,
    ServiceError,
    UnredactableContentError,
    VLMUnavailableError,
)
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.ingest import DocumentError
from anonymization_pipeline.logging_setup import log_event
from anonymization_pipeline.pipeline import (
    MissingKeyError,
    Progress,
    anonymize,
    restore,
    restore_detail,
)
from anonymization_pipeline.pipeline_config import (
    PHASES,
    PipelineConfig,
    effective,
    load_pipeline_config,
    registry,
    request_overrides,
)
from anonymization_pipeline.redaction.vault import VaultError, sha256
from anonymization_pipeline.services.jobs import Job
from anonymization_pipeline.verification import UnredactableContent
from anonymization_pipeline.vlm import VLMClient, VLMError

logger = logging.getLogger(__name__)

ClientFactory = Callable[[PipelineConfig], VLMClient]


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in (value or "").split(",") if x.strip())


class AnonymizationService:
    """Runs the pipeline for the API and the jobs.

    Args:
        settings: Service settings.
        client_factory: Builds the VLM client of one run (on the shared HTTP pool).
        slots: Documents processed at the same time (shared by requests and jobs).
    """

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory,
        slots: asyncio.Semaphore,
    ) -> None:
        self.settings = settings
        self.config = load_pipeline_config(settings)
        self._client_factory = client_factory
        self._slots = slots

    # --- configuration -------------------------------------------------------------------------

    async def request_config(
        self,
        mode: str | None = None,
        verification: str | None = None,
        extraction: str | None = None,
        models: dict[str, str] | None = None,
        enable: str | None = None,
        disable: str | None = None,
        transcript: bool | None = None,
        detectors: str | None = None,
    ) -> PipelineConfig:
        """Configuration of one request.

        Args:
            models: VLM of some phases (phase -> name; a phase not listed uses its default).
            detectors: Active detectors and filters, comma separated (empty = the configured
                ones).

        Raises:
            BadRequestError: Invalid option, transcript not allowed, model not in the models file
                or not in the backend.
            VLMUnavailableError: A model other than the default was chosen and the backend cannot
                list its models.
        """
        if transcript and not self.settings.allow_transcript:
            raise BadRequestError(
                "Trascrizione in chiaro non consentita in questo ambiente."
            )
        chosen = models or {}
        if any(name not in self.config.models for name in chosen.values()):
            raise BadRequestError("Modello non previsto (vedi GET /models).")
        try:
            overrides = request_overrides(
                mode,
                verification,
                chosen,
                _split(enable),
                _split(disable),
                extraction,
                transcript,
                _split(detectors) or None,
            )
            cfg = load_pipeline_config(self.settings, overrides)
        except ValueError as exc:
            # generic message: the user input is never echoed back
            raise BadRequestError(
                "Opzioni non valide: controllare modalità (pseudonym, tag, redaction), verifica "
                "(off, report, fix, block), estrazione (docling, vlm), categorie (GET /categories) e "
                "rilevatori (vlm, rules, labels, ner, eponyms: almeno uno tra i primi quattro)."
            ) from exc
        others = {
            name
            for phase, name in chosen.items()
            if name != self.config.model_for(phase)[0]
        }
        if others:
            # the default models are checked by /readyz; the others must already be in the backend
            listing = await self.models()
            if listing["error"]:
                raise VLMUnavailableError()
            present = {m["name"] for m in listing["models"] if m["present"]}
            if not others <= present:
                raise BadRequestError(
                    "Modello non presente nel backend (vedi GET /models)."
                )
        return cfg

    def key(self, *candidates: str | None) -> str | None:
        """First key given by the request, otherwise the service one (``ANONPIP_VAULT_KEY``)."""
        return (
            next((k for k in candidates if k), None) or self.settings.vault_key or None
        )

    # --- anonymization -------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def slot(self, timeout: float | None) -> AsyncIterator[None]:
        """A processing slot (the GPU is the bottleneck).

        Raises:
            ServerBusyError: No free slot within ``timeout`` seconds (None: wait forever).
        """
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout)
        except TimeoutError as exc:
            raise ServerBusyError() from exc
        try:
            yield
        finally:
            self._slots.release()

    async def run(
        self,
        name: str,
        data: bytes,
        cfg: PipelineConfig,
        key: str | None,
        progress: Progress | None = None,
    ) -> Result:
        """Anonymize one document (the caller holds a slot).

        Raises:
            BadRequestError: Pseudonym mode without a key.
            InvalidDocumentError: Empty, unsupported, unreadable or too large file.
            UnredactableContentError: Data left in clear text (fail-closed).
            VLMUnavailableError: The VLM backend failed.
        """
        # the fingerprint, never the file name: it often holds the patient's name
        document = sha256(data)[:12]
        try:
            result = await anonymize(
                name, data, cfg, self._client_factory(cfg), key, progress
            )
        except MissingKeyError as exc:
            raise BadRequestError(str(exc)) from exc
        except DocumentError as exc:
            raise InvalidDocumentError(str(exc)) from exc
        except UnredactableContent as exc:
            log_event(
                logger,
                "document_rejected",
                {"document": document, "reason": exc.reason},
            )
            raise UnredactableContentError(extra=exc.as_dict()) from exc
        except VLMError as exc:
            logger.error(
                "VLM backend unavailable for document %s: %s",
                document,
                type(exc).__name__,
            )
            raise VLMUnavailableError() from exc
        report = result.report
        log_event(
            logger,
            "document_anonymized",
            {
                "document": document,
                "kind": report["document"]["kind"],
                "pages": report["document"]["pages"],
                "mode": report["mode"],
                "verification": report["verification"]["outcome"],
                "counts": report["counts"],
                "total_s": report["times_s"]["total"],
            },
        )
        return result

    async def anonymize_now(
        self, name: str, data: bytes, cfg: PipelineConfig, key: str | None
    ) -> Result:
        """Synchronous request: wait at most ``job_wait_s`` for a slot, then run."""
        queued = asyncio.get_running_loop().time()
        async with self.slot(self.settings.job_wait_s):
            waited = asyncio.get_running_loop().time() - queued
            result = await self.run(name, data, cfg, key)
        if result.metrics is not None:
            result.metrics["times_s"]["slot_wait"] = round(waited, 2)
        return result

    async def run_job(self, job: Job, progress: Progress) -> Result:
        """Run a queued job: wait for a slot as long as needed."""
        if job.data is None:
            raise ServiceError()  # released already: the job was deleted
        async with self.slot(None):
            return await self.run(job.name, job.data, job.cfg, job.key, progress)

    # --- restore -------------------------------------------------------------------------------

    def restore(self, markdown: str, encrypted_map: bytes, key: str) -> str:
        """Original text.

        Raises:
            RestoreError: Wrong key, invalid or tampered map, modified text.
        """
        try:
            return restore(markdown, encrypted_map, key)
        except VaultError as exc:
            raise RestoreError(str(exc)) from exc

    def restore_detail(
        self, markdown: str, encrypted_map: bytes, key: str
    ) -> dict[str, Any]:
        """Original text with the position of every tag in the redacted and restored text.

        Raises:
            RestoreError: Wrong key, invalid or tampered map, modified text.
        """
        try:
            return restore_detail(markdown, encrypted_map, key, self.config)
        except VaultError as exc:
            raise RestoreError(str(exc)) from exc

    # --- status --------------------------------------------------------------------------------

    async def models(self) -> dict[str, Any]:
        """The models file catalog (a request may choose one per phase), whether each model is in
        the backend, and the default model of each phase. With the backend unreachable
        ``present`` is None and ``error`` says why."""
        client = self._client_factory(self.config)
        try:
            available: set[str] | None = await client.models_available()
            error = None
        except (httpx.HTTPError, VLMError) as exc:
            available, error = None, type(exc).__name__
        return {
            "defaults": {phase: self.config.model_for(phase)[0] for phase in PHASES},
            "models": [
                {
                    "name": name,
                    "context": spec.context,
                    "present": None
                    if available is None
                    else client.has_model(available, name),
                }
                for name, spec in self.config.models.items()
            ],
            "error": error,
        }

    def categories(self) -> dict[str, Any]:
        reg = registry()
        return {
            "groups": {g: list(c) for g, c in reg.groups.items()},
            "active": self.config.active(),
            "descriptions": reg.descriptions,
        }

    def configuration(self) -> dict[str, Any]:
        return effective(self.config)

    def _detectors(self) -> dict[str, Any]:
        d = self.config.phases.detection.detectors
        # presence also when off by default: a request may switch NER on
        ner: dict[str, Any] = {"enabled": d.ner.enabled, "model": d.ner.model}
        try:
            model_dir(d.ner)
            ner.update(present=True, error=None)
        except DetectorUnavailable as exc:
            ner.update(present=False, error=str(exc))
        return {
            "vlm": d.vlm,
            "rules": d.rules,
            "labels": d.labels,
            "eponyms": d.eponyms,
            "ner": ner,
        }

    def _docling(self) -> dict[str, Any]:
        c = self.config.phases.extraction.native_pdf
        if c.engine != "docling":
            return {"enabled": False}
        return {
            "enabled": True,
            "installed": native_pdf.DOCLING_INSTALLED,
            "models": native_pdf.models_present(c),
        }

    async def status(self) -> dict[str, Any]:
        """Readiness and components: the service is ready when the VLM backend answers and has the
        default model of every phase. Missing NER or docling only degrade the service (with
        warnings)."""
        listing = await self.models()
        reachable, error = listing["error"] is None, listing["error"]
        models = [
            {
                "name": m["name"],
                "present": m["present"],
                "phases": [
                    p for p, name in listing["defaults"].items() if name == m["name"]
                ],
            }
            for m in listing["models"]
        ]
        present = all(m["present"] for m in models if m["phases"])
        detectors, docling = await asyncio.to_thread(
            lambda: (self._detectors(), self._docling())
        )
        return {
            "status": "ready" if reachable and present else "unavailable",
            "version": __version__,
            "backend": {
                "kind": self.config.backend.kind,
                "reachable": reachable,
                "error": error,
            },
            "models": models,
            "detectors": detectors,
            "docling": docling,
            "mode": self.config.phases.redaction.mode,
            "verification": self.config.phases.verification.mode,
            "active_categories": len(self.config.active()),
            "warnings": self.settings.obsolete(),
        }


def get_anonymization_service(request: Request) -> AnonymizationService:
    """FastAPI dependency: the service built in the lifespan."""
    return request.app.state.anonymization


AnonymizationServiceDep = Annotated[
    AnonymizationService, Depends(get_anonymization_service)
]
