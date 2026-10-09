import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline import __version__
from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import NotFoundError
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.main import create_app
from anonymization_pipeline.redaction.vault import generate_key
from anonymization_pipeline.vlm import VLMError
from tests.conftest import FAKE_CF, make_settings
from tests.pipeline import FIXTURES, models_file
from tests.unit.conftest import TEXT, VALUES, FakeBackend

KEY = generate_key()
REDACTED = "Paziente: [PERSONA_1]\nCF: [CODICE_FISCALE_1]\n"

ClientFactory = Callable[..., TestClient]


def _vlm_only(tmp_path: Path) -> str:
    """Pipeline file with only the VLM detector (rules and labels would find the values at once)."""
    path = tmp_path / "vlm_only.yaml"
    path.write_text(
        "phases: {detection: {detectors: {rules: false, labels: false, ner: {enabled: false}}}}\n"
    )
    return str(path)


def _upload(text: str = TEXT, name: str = "nota.txt") -> dict:
    return {"file": (name, text.encode())}


# --- probes and template behaviour -------------------------------------------------------------


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reflects_the_vlm_backend(
    backend: FakeBackend, client: TestClient
) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["backend"] == {
        "kind": "ollama",
        "reachable": False,
        "error": "VLMError",
    }

    backend.available = {"other:1b"}
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["models"] == [
        {
            "name": "gemma4:26b-a4b-it-qat",
            "present": False,
            "phases": ["extraction", "detection", "redaction", "verification"],
        }
    ]

    backend.available = {"gemma4:26b-a4b-it-qat"}
    body = client.get("/readyz").json()
    assert body["status"] == "ready" and body["models"][0]["present"] is True
    assert (
        body["detectors"]["ner"]["enabled"] is True
        and body["docling"]["enabled"] is True
    )


def test_version(client: TestClient) -> None:
    assert client.get("/version").json() == {
        "name": "anonymization-pipeline",
        "version": __version__,
    }


def test_unhandled_error_is_generic(
    settings: Settings, log_stream: io.StringIO
) -> None:
    app = create_app(settings, vlm_client_factory=FakeBackend().client)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"failure for {FAKE_CF}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert FAKE_CF not in response.text
    assert "RuntimeError" in log_stream.getvalue()
    assert FAKE_CF not in log_stream.getvalue()


def test_request_id_is_propagated(settings: Settings, log_stream: io.StringIO) -> None:
    app = create_app(settings, vlm_client_factory=FakeBackend().client)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError()

    with TestClient(app) as client:
        response = client.get("/missing", headers={"X-Request-ID": "abcdef123456"})

    assert response.headers["x-request-id"] == "abcdef123456"
    assert response.json() == {"code": "NOT_FOUND", "detail": "Risorsa non trovata."}
    assert "[abcdef123456]" in log_stream.getvalue()


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": f"cf {FAKE_CF}"})

    assert FAKE_CF not in response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 32


def test_docs_disabled_in_prod() -> None:
    app = create_app(
        make_settings(environment="prod"), vlm_client_factory=FakeBackend().client
    )
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404


def test_invalid_pipeline_file_stops_the_startup(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("phases: {redaction: {mode: erase}}\n")
    app = create_app(make_settings(pipeline_config_file=str(path)))

    with pytest.raises(ValueError, match="phases.redaction.mode"), TestClient(app):
        pass


# --- anonymization and restore -------------------------------------------------------------------


def test_anonymize_zip_and_restore(client: TestClient) -> None:
    response = client.post("/anonymize", files=_upload(), data={"key": KEY})

    assert response.status_code == 200, response.text
    assert "nota.anonimizzato.zip" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == [
        "nota.anon.md",
        "nota.mappa.enc",
        "nota.metriche.json",
        "nota.report.json",
    ]
    markdown = archive.read("nota.anon.md").decode()
    assert markdown == REDACTED
    assert "Mario" not in archive.read("nota.report.json").decode()
    files = {
        "markdown": ("nota.anon.md", markdown.encode()),
        "map": ("m", archive.read("nota.mappa.enc")),
    }

    restored = client.post("/restore", files=files, headers={"X-Anon-Key": KEY})
    assert restored.status_code == 200 and restored.text == TEXT

    wrong = client.post("/restore", files=files, data={"key": generate_key()})
    assert wrong.status_code == 403 and wrong.json()["code"] == "RESTORE_FAILED"
    assert client.post("/restore", files=files).status_code == 400  # no key


def test_restore_with_detail(client: TestClient) -> None:
    archive = zipfile.ZipFile(
        io.BytesIO(
            client.post("/anonymize", files=_upload(), data={"key": KEY}).content
        )
    )
    markdown, encrypted = archive.read("nota.anon.md"), archive.read("nota.mappa.enc")
    files = {"markdown": ("a", markdown), "map": ("m", encrypted)}

    response = client.post("/restore/detail", files=files, data={"key": KEY})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == TEXT
    assert {o["category"] for o in body["occurrences"]} == {"PERSONA", "CODICE_FISCALE"}
    for o in body["occurrences"]:
        assert markdown.decode()[o["start"] : o["end"]] == o["replacement"]
        assert body["text"][o["original_start"] : o["original_end"]] == o["original"]
    assert (
        client.post(
            "/restore/detail", files=files, data={"key": generate_key()}
        ).status_code
        == 403
    )


def test_restore_rejects_non_utf8_markdown(client: TestClient) -> None:
    files = {"markdown": ("a", b"\xff\xfe\x00"), "map": ("m", b"{}")}

    response = client.post("/restore", files=files, data={"key": KEY})

    assert response.status_code == 400 and "UTF-8" in response.json()["detail"]


def test_service_key_is_used_when_the_request_has_none(
    make_client: ClientFactory,
) -> None:
    client = make_client(vault_key=KEY)

    archive = zipfile.ZipFile(
        io.BytesIO(client.post("/anonymize", files=_upload()).content)
    )
    files = {
        "markdown": ("a", archive.read("nota.anon.md")),
        "map": ("m", archive.read("nota.mappa.enc")),
    }

    assert client.post("/restore", files=files).text == TEXT


def test_categories(client: TestClient) -> None:
    body = client.get("/categories").json()

    assert (
        "CODICE_FISCALE" in body["groups"]["identificativi"]
        and "CODICE_FISCALE" in body["active"]
    )
    assert body["descriptions"]["CODICE_FISCALE"]


def test_obsolete_settings_are_shown_in_readiness(
    backend: FakeBackend, make_client: ClientFactory
) -> None:
    backend.available = {"gemma4:26b-a4b-it-qat"}
    client = make_client(vlm_model="gemma4:e4b-it-qat")

    body = client.get("/readyz").json()

    assert body["status"] == "ready"  # the models file applies: only a warning
    assert body["warnings"] == ["ANONPIP_VLM_MODEL"]
    assert make_client().get("/readyz").json()["warnings"] == []


def test_ner_presence_is_reported_also_when_off_by_default(
    make_client: ClientFactory, tmp_path: Path
) -> None:
    path = tmp_path / "ner.yaml"
    path.write_text(
        "phases: {detection: {detectors: {ner: "
        f"{{enabled: false, model: '{tmp_path / 'missing'}'}}}}}}}}\n"
    )
    client = make_client(pipeline_config_file=str(path))

    ner = client.get("/readyz").json()["detectors"]["ner"]

    assert (
        ner["enabled"] is False and ner["present"] is False
    )  # a request may switch it on


def test_obsolete_model_field_is_rejected(client: TestClient) -> None:
    for path in ("/anonymize", "/jobs"):
        response = client.post(
            path, files=_upload(), data={"mode": "tag", "model": "gemma4:e4b-it-qat"}
        )
        assert response.status_code == 400
        assert "model_detection" in response.json()["detail"]
        assert "gemma4:e4b-it-qat" not in response.json()["detail"]


def test_configuration_hides_secrets(make_client: ClientFactory) -> None:
    body = make_client(vlm_api_key="secret").get("/configuration").json()

    assert (
        body["phases"]["detection"]["model"] == "gemma4:26b-a4b-it-qat"
        and "gemma4:26b-a4b-it-qat" in body["models"]
        and body["backend"]["api_key"] == "***"
    )


def test_anonymize_json_and_overrides(client: TestClient) -> None:
    response = client.post(
        "/anonymize?format=json",
        files=_upload(),
        data={"mode": "tag", "disable": "CODICE_FISCALE"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["markdown"] == f"Paziente: [PERSONA_1]\nCF: {FAKE_CF}\n"
    assert body["encrypted_map_b64"] is None and body["report"]["mode"] == "tag"
    assert body["file_names"] == {
        "markdown": "nota.anon.md",
        "report": "nota.report.json",
        "metrics": "nota.metriche.json",
    }
    assert "transcript" not in body


def test_errors(
    backend: FakeBackend, make_client: ClientFactory, tmp_path: Path
) -> None:
    client = make_client()
    # missing key in pseudonym mode, unknown category, empty file, invalid mode
    assert client.post("/anonymize", files=_upload("x")).status_code == 400
    unknown = client.post(
        "/anonymize", files=_upload("x"), data={"mode": "tag", "enable": "NON_ESISTE"}
    )
    assert (
        unknown.status_code == 400 and "NON_ESISTE" not in unknown.text
    )  # input never echoed
    assert (
        client.post("/anonymize", files=_upload(""), data={"mode": "tag"}).status_code
        == 400
    )
    assert (
        client.post("/anonymize", files=_upload(), data={"mode": "erase"}).status_code
        == 400
    )
    assert (
        client.post(
            "/anonymize", files=_upload(name="x.docx"), data={"mode": "tag"}
        ).status_code
        == 400
    )

    # residuals in block mode -> 422 without values (only the VLM detector)
    client = make_client(pipeline_config_file=_vlm_only(tmp_path))
    backend.model.missed = {FAKE_CF}
    response = client.post(
        "/anonymize", files=_upload(), data={"mode": "tag", "verification": "block"}
    )
    assert response.status_code == 422
    assert response.json()["residuals"] == [{"category": "CODICE_FISCALE", "page": 1}]
    assert response.json()["reason"] == "residuals"
    assert FAKE_CF not in response.text

    # backend down -> 503
    backend.model.errors = {"detection": VLMError("connection refused")}
    response = client.post("/anonymize", files=_upload(), data={"mode": "tag"})
    assert response.status_code == 503 and response.json()["code"] == "VLM_UNAVAILABLE"


def test_upload_limits(make_client: ClientFactory) -> None:
    client = make_client(max_upload_mb=0.0001)  # about 100 bytes per file

    assert (
        client.post(
            "/anonymize", files=_upload(TEXT * 10), data={"mode": "tag"}
        ).status_code
        == 413
    )
    # over twice the file limit plus the multipart overhead: rejected before the body is read
    too_big = b"x" * (1024 * 1024 + 400)
    assert (
        client.post(
            "/anonymize", content=too_big, headers={"content-type": "text/plain"}
        ).status_code
        == 413
    )
    response = client.post(
        "/anonymize", content=iter([b"x"]), headers={"content-type": "text/plain"}
    )
    assert response.status_code == 411


def test_extraction_chosen_per_request(
    backend: FakeBackend, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field 'extraction': with vlm the native pages go to the VLM (reason vlm_engine)."""
    monkeypatch.setattr(
        native_pdf,
        "convert",
        lambda data, pages, c: {i: "Paziente: Mario Rossi" for i in pages},
    )
    pdf = (FIXTURES / "referto_pdf_nativo.pdf").read_bytes()
    backend.model.markdown = {
        1448: "Paziente: Mario Rossi"
    }  # width of the page sent to the VLM (max side 2048)
    expected = {
        "docling": {"docling": [1], "vlm": []},
        "vlm": {"docling": [], "vlm": [{"page": 1, "reason": "vlm_engine"}]},
    }

    for engine, extraction in expected.items():
        response = client.post(
            "/anonymize?format=json",
            files={"file": ("r.pdf", pdf)},
            data={"mode": "tag", "extraction": engine},
        )
        assert response.status_code == 200, response.text
        assert response.json()["report"]["extraction"] == extraction
    invalid = client.post(
        "/anonymize",
        files={"file": ("r.pdf", pdf)},
        data={"mode": "tag", "extraction": "ocr"},
    )
    assert invalid.status_code == 400


def test_detectors_per_request(backend: FakeBackend, client: TestClient) -> None:
    backend.available = {"gemma4:26b-a4b-it-qat"}

    response = client.post(
        "/anonymize?format=json",
        files=_upload(),
        data={"mode": "tag", "detectors": "rules,labels"},
    )

    assert response.status_code == 200
    report = response.json()["report"]
    assert set(report["detection"]["sources"]) == {"rules", "labels"}
    assert "detection" not in report["vlm"]  # no VLM call in detection
    assert response.json()["metrics"]["configuration"]["eponym_filter"] is False


@pytest.mark.parametrize("detectors", ["rules,ocr_segreto", "eponyms"])
def test_invalid_detectors_are_rejected(client: TestClient, detectors: str) -> None:
    for path in ("/anonymize", "/jobs"):
        response = client.post(
            path, files=_upload(), data={"mode": "tag", "detectors": detectors}
        )
        assert response.status_code == 400 and "rilevatori" in response.json()["detail"]
        assert "ocr_segreto" not in response.json()["detail"]  # never echoed


CATALOG = {"big:1": 16384, "small:1": 4096, "spare:1": 4096}


def _catalog_client(make_client: ClientFactory, tmp_path: Path) -> TestClient:
    return make_client(
        models_file=models_file(tmp_path, CATALOG, {"detection": "small:1"})
    )


def test_model_per_phase(
    backend: FakeBackend, make_client: ClientFactory, tmp_path: Path
) -> None:
    client = _catalog_client(make_client, tmp_path)
    backend.available = {"big:1", "small:latest", "other:7b"}

    body = client.get("/models").json()
    assert body == {
        "defaults": {
            "extraction": "big:1",
            "detection": "small:1",
            "redaction": "big:1",
            "verification": "big:1",
        },
        "models": [  # only the catalog: other:7b is not offered
            {"name": "big:1", "context": 16384, "present": True},
            {"name": "small:1", "context": 4096, "present": False},
            {"name": "spare:1", "context": 4096, "present": False},
        ],
        "error": None,
    }

    backend.available = {"big:1", "small:1"}
    response = client.post(
        "/anonymize?format=json",
        files=_upload(),
        data={"mode": "tag", "model_detection": "big:1", "model_redaction": "small:1"},
    )
    assert response.status_code == 200
    assert response.json()["report"]["model"]["phases"] == {
        "extraction": "big:1",
        "detection": "big:1",
        "redaction": "small:1",
        "verification": "big:1",
    }


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (
            {"model_detection": "other:7b"},
            "non previsto",
        ),  # in the backend, not in the catalog
        (
            {"model_verification": "spare:1"},
            "non presente",
        ),  # in the catalog, not in the backend
    ],
)
def test_model_outside_the_catalog_or_the_backend_is_rejected(
    backend: FakeBackend,
    make_client: ClientFactory,
    tmp_path: Path,
    data: dict[str, str],
    detail: str,
) -> None:
    client = _catalog_client(make_client, tmp_path)
    backend.available = {"big:1", "small:1", "other:7b"}

    for path in ("/anonymize", "/jobs"):
        response = client.post(path, files=_upload(), data={"mode": "tag", **data})
        assert response.status_code == 400 and detail in response.json()["detail"]
        assert data[next(iter(data))] not in response.json()["detail"]  # never echoed


def test_models_with_an_unreachable_backend(
    make_client: ClientFactory, tmp_path: Path
) -> None:
    client = _catalog_client(make_client, tmp_path)

    body = client.get("/models").json()  # the error is reported, not raised

    assert body["error"] == "VLMError"
    assert {m["name"]: m["present"] for m in body["models"]} == dict.fromkeys(CATALOG)
    response = client.post(
        "/anonymize", files=_upload(), data={"mode": "tag", "model_detection": "big:1"}
    )
    assert response.status_code == 503
    # the default model of the phase needs no check of the backend
    response = client.post(
        "/jobs", files=_upload(), data={"mode": "tag", "model_detection": "small:1"}
    )
    assert response.status_code == 202


def test_busy_server_answers_503(make_client: ClientFactory) -> None:
    client = make_client(job_wait_s=0)
    service = client.app.state.anonymization
    service._slots._value = 0  # every slot taken

    response = client.post("/anonymize", files=_upload(), data={"mode": "tag"})

    assert response.status_code == 503 and response.headers["retry-after"] == "30"


# --- privacy -------------------------------------------------------------------------------------


def test_anonymization_never_logs_document_content(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.post(
        "/anonymize?format=json",
        files=_upload(name="Rossi_Mario.txt"),
        data={"key": KEY},
    )

    assert response.status_code == 200
    logs = log_stream.getvalue()
    assert "document_anonymized" in logs
    for value in [*VALUES, "Rossi_Mario", KEY]:
        assert value not in logs


def test_rejection_never_logs_document_content(
    backend: FakeBackend,
    make_client: ClientFactory,
    log_stream: io.StringIO,
    tmp_path: Path,
) -> None:
    client = make_client(pipeline_config_file=_vlm_only(tmp_path))
    backend.model.missed = {FAKE_CF}

    response = client.post(
        "/anonymize", files=_upload(), data={"mode": "tag", "verification": "block"}
    )

    assert response.status_code == 422
    logs = log_stream.getvalue()
    assert "document_rejected" in logs
    for value in VALUES:
        assert value not in logs


def _events(logs: str, name: str) -> list[dict]:
    return [
        json.loads(line.split(f"{name} ", 1)[1])
        for line in logs.splitlines()
        if f": {name} {{" in line
    ]


def test_dashboard_events_keep_their_names_and_keys(
    backend: FakeBackend,
    make_client: ClientFactory,
    log_stream: io.StringIO,
    tmp_path: Path,
) -> None:
    client = make_client()
    client.post("/anonymize", files=_upload(), data={"mode": "tag"})
    client = make_client(pipeline_config_file=_vlm_only(tmp_path))
    backend.model.missed = {FAKE_CF}
    client.post(
        "/anonymize", files=_upload(), data={"mode": "tag", "verification": "block"}
    )

    logs = log_stream.getvalue()
    (anonymized,) = _events(logs, "document_anonymized")
    assert set(anonymized) == {
        "document",
        "kind",
        "pages",
        "mode",
        "verification",
        "counts",
        "total_s",
    }
    assert (
        anonymized["counts"] == {"CODICE_FISCALE": 1, "PERSONA": 1}
        and len(anonymized["document"]) == 12
    )
    (rejected,) = _events(logs, "document_rejected")
    assert rejected == {"document": anonymized["document"], "reason": "residuals"}
