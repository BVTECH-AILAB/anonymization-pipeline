"""Restore: the original values put back into an anonymized Markdown, with its map and key.

The answer holds the original personal data by design: who may call these endpoints and with
which audit is an open point (PA-03, PA-06); no value or key is ever logged.
"""

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from anonymization_pipeline.api.uploads import read_markdown, read_upload
from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import BadRequestError, RestoreFailedError
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.render import vault
from anonymization_pipeline.text import tag_category

router = APIRouter(prefix="/restore", tags=["restore"])

Key = Annotated[str, Form(max_length=500, description="key or passphrase of the map")]
KeyHeader = Annotated[str, Header(max_length=500)]


class RestoredOccurrence(BaseModel):
    start: int
    end: int
    replacement: str
    original: str
    original_start: int
    original_end: int
    category: str | None


class Restored(BaseModel):
    text: str
    occurrences: list[RestoredOccurrence]


async def _open(
    request: Request,
    markdown: UploadFile,
    map_file: UploadFile,
    key: str,
    x_anon_key: str,
) -> tuple[str, dict[str, Any]]:
    settings: Settings = request.app.state.settings
    secret = key or x_anon_key or settings.vault_key
    if not secret:
        raise BadRequestError("Chiave mancante.")
    text = await read_markdown(markdown, settings)
    blob = await read_upload(map_file, settings)
    try:
        return text, vault.decrypt_map(blob, secret)
    except vault.VaultError as exc:  # Italian message, never the key or a value
        raise RestoreFailedError(str(exc)) from exc


@router.post("", response_class=PlainTextResponse)
async def restore(
    request: Request,
    markdown: Annotated[UploadFile, File(description="anonymized Markdown")],
    map_file: Annotated[UploadFile, File(alias="map", description="encrypted map")],
    key: Key = "",
    x_anon_key: KeyHeader = "",
) -> str:
    """The original text of an anonymized Markdown (pseudonym mode)."""
    text, content = await _open(request, markdown, map_file, key, x_anon_key)
    try:
        return vault.restore(text, content)
    except vault.VaultError as exc:
        raise RestoreFailedError(str(exc)) from exc


@router.post("/detail")
async def restore_detail(
    request: Request,
    markdown: Annotated[UploadFile, File(description="anonymized Markdown")],
    map_file: Annotated[UploadFile, File(alias="map", description="encrypted map")],
    key: Key = "",
    x_anon_key: KeyHeader = "",
) -> Restored:
    """The original text and the position of every tag in both texts, with its category."""
    text, content = await _open(request, markdown, map_file, key, x_anon_key)
    cfg: PipelineConfig = request.app.state.pipeline_config
    try:
        restored, positions = vault.restore_detail(text, content)
    except vault.VaultError as exc:
        raise RestoreFailedError(str(exc)) from exc
    return Restored(
        text=restored,
        occurrences=[
            RestoredOccurrence(
                **p, category=tag_category(p["replacement"], cfg.redaction)
            )
            for p in positions
        ],
    )
