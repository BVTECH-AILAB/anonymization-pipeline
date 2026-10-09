"""Serialization of an anonymization result: JSON body and ZIP archive."""

import base64
import io
import json
import re
import zipfile
from typing import Any
from urllib.parse import quote

from anonymization_pipeline.domain import Result


def result_payload(result: Result) -> dict[str, Any]:
    """JSON body of a result. The transcript and the clear-text occurrences only when requested."""
    payload: dict[str, Any] = {
        "file_names": result.output_names(),
        "markdown": result.markdown,
        "report": result.report,
        "metrics": result.metrics,
        "tags": result.tags,
        "encrypted_map_b64": base64.b64encode(result.encrypted_map).decode()
        if result.encrypted_map
        else None,
    }
    if result.transcript is not None:  # CLEAR TEXT: only when asked for
        payload.update(transcript=result.transcript, occurrences=result.occurrences)
    return payload


def zip_result(result: Result) -> bytes:
    """ZIP with the redacted Markdown, the report and, when present, map, metrics and transcript."""
    buffer = io.BytesIO()
    names = result.output_names()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(names["markdown"], result.markdown)
        archive.writestr(
            names["report"], json.dumps(result.report, indent=2, ensure_ascii=False)
        )
        if result.encrypted_map is not None:
            archive.writestr(names["map"], result.encrypted_map)
        if result.metrics is not None:
            archive.writestr(
                names["metrics"],
                json.dumps(result.metrics, indent=2, ensure_ascii=False),
            )
        if result.transcript is not None:
            archive.writestr(names["transcript"], result.transcript)
    return buffer.getvalue()


def content_disposition(name: str) -> str:
    """Attachment header with an ASCII fallback and the UTF-8 name (RFC 6266)."""
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", name) or "documento"
    return (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
    )


def zip_name(result: Result) -> str:
    return f"{result.name.rsplit('.', 1)[0]}.anonimizzato.zip"
