"""Synchronous anonymization and restore endpoints."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from anonymization_pipeline.api.uploads import read_markdown, read_upload
from anonymization_pipeline.errors import BadRequestError
from anonymization_pipeline.services.anonymization import AnonymizationServiceDep
from anonymization_pipeline.services.results import (
    content_disposition,
    result_payload,
    zip_name,
    zip_result,
)

router = APIRouter(tags=["anonymization"])

Mode = Annotated[
    str | None, Form(max_length=20, description="pseudonym | tag | redaction")
]
Verification = Annotated[
    str | None, Form(max_length=20, description="off | report | block")
]
Extraction = Annotated[
    str | None, Form(max_length=20, description="native PDF pages: docling | vlm")
]
Model = Annotated[
    str | None,
    Form(max_length=200, description="VLM of the phase (one of GET /models)"),
]
Categories = Annotated[
    str | None,
    Form(max_length=2000, description="categories or groups, comma separated"),
]
Detectors = Annotated[
    str | None,
    Form(
        max_length=200,
        description="active detectors and filters, comma separated: vlm, rules, labels, ner, "
        "eponyms (the others are off; empty = the configured ones)",
    ),
]
Layout = Annotated[
    bool | None,
    Form(
        description="also the PDF with the layout of the original and its structure "
        "(empty = the configured default)"
    ),
]
Key = Annotated[
    str | None,
    Form(max_length=200, description="key or passphrase of the map (or X-Anon-Key)"),
]
KeyHeader = Annotated[str | None, Header(max_length=200)]


def phase_models(
    extraction: str | None,
    detection: str | None,
    verification: str | None,
) -> dict[str, str]:
    """The ``model_<phase>`` form fields as phase -> model, only the phases with a value."""
    chosen = {
        "extraction": extraction,
        "detection": detection,
        "verification": verification,
    }
    return {phase: name for phase, name in chosen.items() if name}


@router.post(
    "/anonymize",
    summary="Anonymize a document",
    description="ZIP with the redacted Markdown, the JSON report, the metrics and, in pseudonym mode, "
    "the encrypted map. With format=json the same content as JSON (map in base64).",
    response_model=None,
)
async def anonymize(
    service: AnonymizationServiceDep,
    file: Annotated[UploadFile, File()],
    mode: Mode = None,
    verification: Verification = None,
    extraction: Extraction = None,
    model_extraction: Model = None,
    model_detection: Model = None,
    model_verification: Model = None,
    enable: Categories = None,
    disable: Categories = None,
    detectors: Detectors = None,
    layout: Layout = None,
    key: Key = None,
    x_anon_key: KeyHeader = None,
    output: Annotated[Literal["zip", "json"], Query(alias="format")] = "zip",
) -> Response:
    models = phase_models(model_extraction, model_detection, model_verification)
    data = await read_upload(file, service.settings)
    cfg = await service.request_config(
        mode,
        verification,
        extraction,
        models,
        enable,
        disable,
        detectors=detectors,
        layout=layout,
    )
    result = await service.anonymize_now(
        file.filename or "documento", data, cfg, service.key(key, x_anon_key)
    )
    if output == "json":
        return JSONResponse(result_payload(result))
    return Response(
        zip_result(result),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(zip_name(result))},
    )


@router.post(
    "/restore",
    summary="Put the original values back from pseudonymized Markdown and map",
    response_class=PlainTextResponse,
)
async def restore(
    service: AnonymizationServiceDep,
    markdown: Annotated[UploadFile, File(description=".anon.md file")],
    map_file: Annotated[UploadFile, File(alias="map", description=".mappa.enc file")],
    key: Key = None,
    x_anon_key: KeyHeader = None,
) -> str:
    secret = service.key(key, x_anon_key)
    if not secret:
        raise BadRequestError("Chiave mancante.")
    text = await read_markdown(markdown, service.settings)
    return service.restore(text, await read_upload(map_file, service.settings), secret)


@router.post(
    "/restore/pdf",
    summary="Rebuild the original PDF from the censored layout PDF and the map",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def restore_pdf(
    service: AnonymizationServiceDep,
    document: Annotated[
        UploadFile, File(description=".anon.pdf file (or its .anon.docling.json)")
    ],
    map_file: Annotated[UploadFile, File(alias="map", description=".mappa.enc file")],
    key: Key = None,
    x_anon_key: KeyHeader = None,
) -> Response:
    """The PDF holds layout, text and tables of the original; images are not rebuilt."""
    secret = service.key(key, x_anon_key)
    if not secret:
        raise BadRequestError("Chiave mancante.")
    pdf = await service.restore_pdf(
        await read_upload(document, service.settings),
        await read_upload(map_file, service.settings),
        secret,
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition("documento.ripristinato.pdf")
        },
    )


@router.post(
    "/restore/detail",
    summary="Restore with the positions of the tags (for the comparison)",
)
async def restore_detail(
    service: AnonymizationServiceDep,
    markdown: Annotated[UploadFile, File(description=".anon.md file")],
    map_file: Annotated[UploadFile, File(alias="map", description=".mappa.enc file")],
    key: Key = None,
    x_anon_key: KeyHeader = None,
) -> dict[str, Any]:
    """JSON ``{text, occurrences: [{start, end, original_start, original_end, replacement, original,
    category}]}``: positions in the redacted Markdown and in the restored text."""
    secret = service.key(key, x_anon_key)
    if not secret:
        raise BadRequestError("Chiave mancante.")
    text = await read_markdown(markdown, service.settings)
    return service.restore_detail(
        text, await read_upload(map_file, service.settings), secret
    )
