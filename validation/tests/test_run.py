"""End-to-end run against a fake service on a one-document dataset."""

import json
import re
from pathlib import Path

import httpx
import pytest

from anonval.run import main_async, parse_args

GOLD = {
    "id": "referto_esterno_facile_01",
    "type": "referto_esterno",
    "difficulty": "facile",
    "pages": 1,
    "entities": [
        {
            "value": "Anna Riva",
            "category": "PERSONA",
            "group": "anagrafica",
            "family": "PERSONA",
            "mentions": 1,
            "locations": ["patient_box"],
            "pages": [1],
        },
        {
            "value": "Ugo Neri",
            "category": "MEDICO",
            "group": "persone_correlate",
            "family": "PERSONA",
            "mentions": 1,
            "locations": ["signature"],
            "pages": [1],
        },
    ],
    "negatives": [],
}
TRANSCRIPT = "Paziente: Anna Riva. Firma: Dott. Ugo Neri."


def _dataset(root: Path) -> None:
    (root / "pdf").mkdir(parents=True)
    (root / "gt").mkdir()
    (root / "pdf" / "d.pdf").write_bytes(b"%PDF-1.7 fake")
    (root / "gt" / "d.json").write_text(json.dumps(GOLD))
    manifest = {
        "documents": [
            {
                **{k: GOLD[k] for k in ("id", "type", "difficulty", "pages")},
                "entities": 2,
                "pdf": "pdf/d.pdf",
                "ground_truth": "gt/d.json",
            }
        ]
    }
    (root / "manifest.json").write_text(json.dumps(manifest))


def _config(path: Path, profiles: str) -> Path:
    config = path / "config.yaml"
    config.write_text(
        "server: {base_url: 'http://svc', poll_interval_s: 0.01}\n"
        "dataset: {percorso: dataset}\n"
        "soglie: {recall_min: 0.4, tasso_fuga_max: 0.0, precision_min: 0.5, f1_min: 0.5}\n"
        "comune: {mode: tag, verification: report}\n"
        f"profili: {profiles}\n"
    )
    return config


class FakeService:
    """The service: tags only "Anna Riva", so "Ugo Neri" leaks."""

    def __init__(self, transcript_allowed: bool = True) -> None:
        self.transcript_allowed = transcript_allowed
        self.forms: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path in (
            "/healthz",
            "/version",
            "/categories",
            "/models",
            "/configuration",
        ):
            return httpx.Response(
                200,
                json={
                    "/healthz": {"status": "ok"},
                    "/version": {"name": "anonymization-pipeline", "version": "0.1.0"},
                    "/categories": {"active": ["PERSONA", "MEDICO"], "groups": {}},
                    "/models": {
                        "defaults": {"detection": "m"},
                        "models": [{"name": "m", "present": True}],
                    },
                    "/configuration": {"phases": {"redaction": {"mode": "pseudonym"}}},
                }[path],
            )
        if request.method == "POST":
            self.forms.append(request.content.decode(errors="ignore"))
            if not self.transcript_allowed:
                return httpx.Response(
                    400,
                    json={
                        "code": "BAD_REQUEST",
                        "detail": "Trascrizione in chiaro non consentita in questo ambiente.",
                    },
                )
            return httpx.Response(202, json={"id": "j", "status": "queued"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if path == "/jobs/j/result":
            start = TRANSCRIPT.index("Anna Riva")
            return httpx.Response(
                200,
                json={
                    "markdown": TRANSCRIPT.replace("Anna Riva", "[PERSONA_1]"),
                    "transcript": TRANSCRIPT,
                    "occurrences": [
                        {
                            "original_start": start,
                            "original_end": start + 9,
                            "original": "Anna Riva",
                            "category": "PERSONA",
                        }
                    ],
                    "report": {
                        "extraction": {"docling": [1], "vlm": []},
                        "verification": {"outcome": "ok", "residuals": []},
                    },
                },
            )
        return httpx.Response(
            200, json={"id": "j", "status": "completed", "duration_s": 1.5}
        )


async def test_run_writes_report_and_fails_on_leak(tmp_path: Path) -> None:
    _dataset(tmp_path / "dataset")
    config = _config(tmp_path, "[{nome: base}, {nome: regole, detectors: [rules]}]")
    service = FakeService()
    args = parse_args(["--config", str(config), "--output", str(tmp_path / "out")])
    code = await main_async(args, httpx.MockTransport(service))
    assert code == 1
    (run_dir,) = (tmp_path / "out").iterdir()
    report = (run_dir / "report.md").read_text()
    assert "## Confronto tra profili" in report
    assert (
        '"Ugo Neri" (MEDICO): in chiaro a pag. 1; nel layout: firma; '
        "nessun rilevatore lo ha trovato"
    ) in report
    metrics = json.loads((run_dir / "metriche.json").read_text())
    base = metrics["profili"][0]["metriche"]["overall"]
    assert (
        base["recall"] == 0.5 and base["tasso_fuga"] == 0.5 and base["precision"] == 1.0
    )
    assert (run_dir / "raw" / "regole" / "referto_esterno_facile_01.json").exists()
    assert re.search(r'name="detectors"\s+rules', service.forms[1])

    # resuming reuses the saved results: no new job
    args = parse_args(["--config", str(config), "--riprendi", str(run_dir)])
    assert await main_async(args, httpx.MockTransport(service)) == 1
    assert len(service.forms) == 2


async def test_refused_profile_is_aborted(tmp_path: Path) -> None:
    _dataset(tmp_path / "dataset")
    config = _config(tmp_path, "[{nome: base}]")
    args = parse_args(["--config", str(config), "--output", str(tmp_path / "out")])
    assert (
        await main_async(
            args, httpx.MockTransport(FakeService(transcript_allowed=False))
        )
        == 1
    )
    (run_dir,) = (tmp_path / "out").iterdir()
    assert "Profilo interrotto" in (run_dir / "report.md").read_text()


@pytest.mark.parametrize(
    ("extra", "profiles"),
    [
        ([], "[{nome: base, mode: pseudonym}]"),
        ([], "[{nome: base, model_detection: altro}]"),
        (["--profilo", "manca"], "[{nome: base}]"),
    ],
)
async def test_start_errors(
    tmp_path: Path, extra: list[str], profiles: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANONVAL_KEY", raising=False)
    _dataset(tmp_path / "dataset")
    config = _config(tmp_path, profiles)
    args = parse_args(
        ["--config", str(config), "--output", str(tmp_path / "out"), *extra]
    )
    assert await main_async(args, httpx.MockTransport(FakeService())) == 2


async def test_unreachable_server(tmp_path: Path) -> None:
    _dataset(tmp_path / "dataset")
    config = _config(tmp_path, "[{nome: base}]")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    args = parse_args(["--config", str(config)])
    assert await main_async(args, httpx.MockTransport(down)) == 2
