"""Run against a real VLM backend: `just test-vlm` (inside the app container of the compose stack).

The backend URL and the model come from the container settings (ANONPIP_VLM_URL, ANONPIP_VLM_MODEL),
e.g. ANONPIP_VLM_MODEL=gemma4:e4b-it-qat and ANONPIP_VLM_CONTEXT=8192 on a small GPU.
"""

import asyncio
import io

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from anonymization_pipeline.config import Settings
from anonymization_pipeline.pipeline import anonymize
from anonymization_pipeline.pipeline_config import load_pipeline_config
from anonymization_pipeline.vlm import create_client
from tests.conftest import FAKE_CF, make_settings

pytestmark = pytest.mark.integration

LINES = [
    "AZIENDA OSPEDALIERA DI MONTEFALCUZZO",
    "Referto ambulatoriale",
    "",
    "Paziente: Mario Rossi",
    f"Codice fiscale: {FAKE_CF}",
    "Telefono: 333 1234567",
    "",
    "Diagnosi: diabete mellito di tipo 2",
    "Terapia: metformina 500 mg due volte al giorno",
    "",
    "Il medico: Dott.ssa Anna Bianchi",
]
SENSITIVE = ["Mario Rossi", FAKE_CF, "333 1234567", "Anna Bianchi"]


def _image() -> bytes:
    image = Image.new("RGB", (1240, 900), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default(size=30)
    for i, line in enumerate(LINES):
        draw.text((60, 50 + i * 70), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_real_pipeline() -> None:
    target = Settings()  # only the backend coordinates come from the environment
    settings = make_settings(
        vlm_url=target.vlm_url,
        vlm_backend=target.vlm_backend,
        vlm_model=target.vlm_model,
        vlm_context=target.vlm_context,
        default_mode="tag",
        default_verification="report",
        docling_models_dir=target.docling_models_dir,
    )
    cfg = load_pipeline_config(settings, {"output": {"save_transcript": True}})

    async def run() -> tuple[str, str]:
        async with httpx.AsyncClient() as http:
            result = await anonymize(
                "referto.png", _image(), cfg, create_client(cfg, http)
            )
        assert result.transcript is not None
        return result.transcript, result.markdown

    transcript, markdown = asyncio.run(run())

    assert "Mario Rossi" in transcript  # the extraction read the text
    for value in SENSITIVE:
        assert value not in markdown, f"{value} in clear text"
