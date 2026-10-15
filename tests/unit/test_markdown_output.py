"""The anonymized Markdown next to the PDF: service, API, encrypted map and restore."""

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline.main import create_app
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.render import markdown as markdown_module
from anonymization_pipeline.render.markdown import Leftover, Rendered
from anonymization_pipeline.render.vault import VaultError, decrypt_map, generate_key
from anonymization_pipeline.services import anonymization as service
from anonymization_pipeline.services.anonymization import anonymize
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings
from tests.documents import EMAIL, PATIENT, PHONE, letter_pdf

KEY = generate_key()


def _cfg(**redaction: Any) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    return cfg.model_copy(
        update={"redaction": cfg.redaction.model_copy(update=redaction)}
    )


@pytest.fixture
def letter(tmp_path: Path) -> bytes:
    return letter_pdf(tmp_path / "in.pdf", pages=2).read_bytes()


def test_markdown_holds_tags_and_no_values(letter: bytes) -> None:
    result = anonymize(letter, _cfg())

    assert result.status == "done" and result.mode == "tag"
    md = result.markdown or ""
    for value in (FAKE_CF, PATIENT, PHONE, EMAIL, "Laura Bianchi"):
        assert value not in md
    assert "[CODICE_FISCALE_1]" in md and "morbo di Crohn" in md
    assert "<!-- pagina 2 -->" in md
    assert md.count("[CODICE_FISCALE_1]") == 2  # the same tag on both pages
    assert result.replacements > 0 and result.encrypted_map is None


def test_pseudonym_map_restores_the_original_text(letter: bytes) -> None:
    result = anonymize(letter, _cfg(mode="pseudonym"), key=KEY)

    assert result.encrypted_map is not None
    content = decrypt_map(result.encrypted_map, KEY)
    originals = {o["originale"] for o in content["occorrenze"]}
    assert {FAKE_CF, PATIENT} <= originals
    assert FAKE_CF.encode() not in result.encrypted_map


def test_pseudonym_mode_needs_a_key(letter: bytes) -> None:
    with pytest.raises(VaultError, match="chiave"):
        anonymize(letter, _cfg(mode="pseudonym"))


def test_redaction_mode_uses_the_symbol(letter: bytes) -> None:
    result = anonymize(letter, _cfg(mode="redaction"))

    assert "█████" in (result.markdown or "") and "[CODICE_FISCALE" not in (
        result.markdown or ""
    )


def test_a_value_left_in_the_markdown_blocks_the_document(
    letter: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaky(
        texts: list[str], values: set[tuple[str, str]], cfg: PipelineConfig
    ) -> tuple[Rendered, list[Leftover]]:
        rendered, _ = markdown_module.render(texts, values, cfg)
        return rendered, [Leftover("CODICE_FISCALE", 1)]

    monkeypatch.setattr(service, "render", leaky)

    result = anonymize(letter, _cfg())

    assert (result.status, result.reason, result.markdown) == (
        "blocked",
        "verification_failed",
        None,
    )
    assert [(f.check, f.page, f.category) for f in result.failures] == [
        ("markdown_leftover", 0, "CODICE_FISCALE")
    ]


def test_markdown_can_be_switched_off(letter: bytes) -> None:
    cfg = _cfg()
    cfg = cfg.model_copy(
        update={"markdown": cfg.markdown.model_copy(update={"enabled": False})}
    )

    result = anonymize(letter, cfg)

    assert result.status == "done" and result.markdown is None


# --- API --------------------------------------------------------------------------------------


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(1500):
        body = client.get(f"/anonymizations/{job_id}").json()
        if body["status"] in ("done", "blocked", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError("job not finished")


@pytest.fixture
def client() -> Any:
    with TestClient(create_app(make_settings()), raise_server_exceptions=False) as c:
        yield c


def _upload(
    client: TestClient, data: bytes, headers: dict[str, str] | None = None, **form: str
) -> Any:
    return client.post(
        "/anonymizations",
        files={"file": ("x.pdf", data)},
        data=form,
        headers=headers or {},
    )


def test_markdown_and_map_are_downloaded_and_restored(
    client: TestClient, letter: bytes, log_stream: io.StringIO
) -> None:
    job = _upload(client, letter, mode="pseudonym", key=KEY).json()["id"]
    body = _wait(client, job)
    assert (
        body["result"]["markdown"],
        body["result"]["map"],
        body["result"]["mode"],
    ) == (
        True,
        True,
        "pseudonym",
    )

    md = client.get(f"/anonymizations/{job}/markdown")
    blob = client.get(f"/anonymizations/{job}/map")
    assert md.headers["content-type"].startswith("text/markdown")
    assert "documento_anonimizzato.md" in md.headers["content-disposition"]
    assert FAKE_CF not in md.text

    files = {"markdown": ("a.md", md.content), "map": ("a.enc", blob.content)}
    restored = client.post("/restore", files=files, data={"key": KEY})
    detail = client.post(
        "/restore/detail", files=files, headers={"X-Anon-Key": KEY}
    ).json()
    assert FAKE_CF in restored.text and PATIENT in restored.text
    assert detail["text"] == restored.text
    cf = next(o for o in detail["occurrences"] if o["original"] == FAKE_CF)
    assert cf["category"] == "CODICE_FISCALE"
    assert detail["text"][cf["original_start"] : cf["original_end"]] == FAKE_CF
    assert KEY not in log_stream.getvalue() and FAKE_CF not in log_stream.getvalue()


def test_wrong_key_and_missing_key(client: TestClient, letter: bytes) -> None:
    job = _upload(client, letter, mode="pseudonym", key=KEY).json()["id"]
    _wait(client, job)
    files = {
        "markdown": ("a.md", client.get(f"/anonymizations/{job}/markdown").content),
        "map": ("a.enc", client.get(f"/anonymizations/{job}/map").content),
    }

    wrong = client.post("/restore", files=files, data={"key": generate_key()})
    missing = client.post("/restore", files=files)

    assert (wrong.status_code, wrong.json()["code"]) == (403, "RESTORE_FAILED")
    assert (missing.status_code, missing.json()["code"]) == (400, "BAD_REQUEST")


def test_pseudonym_without_key_is_refused_at_upload(
    client: TestClient, letter: bytes
) -> None:
    response = _upload(client, letter, mode="pseudonym")

    assert (response.status_code, response.json()["code"]) == (400, "BAD_REQUEST")
    assert "chiave" in response.json()["detail"]


def test_unknown_mode_is_refused(client: TestClient, letter: bytes) -> None:
    response = _upload(client, letter, mode="cancella")

    assert (
        response.status_code == 400
        and "Modalità sconosciuta" in response.json()["detail"]
    )


def test_tag_mode_has_no_map(client: TestClient, letter: bytes) -> None:
    job = _upload(client, letter).json()["id"]
    body = _wait(client, job)

    assert body["result"]["map"] is False
    assert client.get(f"/anonymizations/{job}/map").status_code == 404
    assert client.get(f"/anonymizations/{job}/markdown").status_code == 200


def test_service_key_is_used_when_the_request_has_none(letter: bytes) -> None:
    with TestClient(create_app(make_settings(vault_key=KEY))) as client:
        job = _upload(client, letter, mode="pseudonym").json()["id"]
        body = _wait(client, job)
        blob = client.get(f"/anonymizations/{job}/map").content

    assert body["status"] == "done"
    assert json.loads(blob)["alg"] == "AES-256-GCM"
    assert decrypt_map(blob, KEY)["occorrenze"]
