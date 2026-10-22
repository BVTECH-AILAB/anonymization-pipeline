# AGENTS.md

Instructions for coding agents working on the anonymization service. Endpoints,
setup and results are in the repository `README.md` and `docs/ANONYMIZATION.md`;
the privacy and logging rules in `docs/LOGGING_AND_PRIVACY.md` always apply.

## Project

The AGENAS MIA service that anonymizes clinical documents: a PDF (native or
scanned) or an image in, a redacted PDF and Word copy (`accurate`) or an
anonymized Markdown text (`fast`) out, each verified before it can be
downloaded (fail-closed). OCR runs in the second pod (`../ocr-server`), NER in
the AILAB NER service.

## Layout

- `src/router/`: HTTP only (parse, call one service, render); `errors.py` turns
  service and engine errors into generic Italian responses
- `src/services/anonymization.py`: the job manager behind the router (queue,
  SSE, expiry, limits, KPI events); takes the engine as an argument
- `src/services/anonymizer/`: the engine, a self-contained sub-package (`ruff.toml`
  bans the router, the session service, the app, `Settings`, any web framework)
- `src/llms/`: clients of external models: `ner.py` (AILAB), `ocr.py` (OCR
  server, `services/anonymizer/OCR_CONTRACT.md`)
- `src/models/`: API wire models only; `src/errors.py`: `ServiceError` subclasses
- `src/config.py`: `Settings` (prefix `ANONPIP_`), `pipeline_config()` for the engine
- `src/lifespan.py`: shared resources on `app.state`; `src/main.py`: `create_app()`
- `src/utils/`: logging setup with the PII filter, privacy helpers, request id
- `tests/` mirrors `src/`; `tests/documents.py` is the test kit (synthetic PDFs,
  fake reader and NER, `OcrServerStub`)
- `evals/`: the evaluation CLI (`python -m evals`), dev only, `evals` group

## Where new code goes

| Adding | Put it in |
|---|---|
| HTTP endpoint | `router/<area>.py` + `services/<area>.py` + `models/<area>.py`; include in `main.py` |
| Detector or engine step | `services/anonymizer/` (or `detect/`), with tests in `tests/services/anonymizer/` |
| Client of an external model or service | `llms/<name>.py`, with a contract test |
| Setting | `config.py` + `.env.example` (+ `docs/DEPLOY_HANDOVER.md`), safe default |

## Rules

- Python 3.12, type hints, Google-style docstrings, imports at module level only.
- Configuration only through `Settings` (`os.getenv` is banned); credentials never have a default.
- Logs: `logging.getLogger(__name__)`, lazy `%s`, never document text, values,
  file names or identifiers; KPI events through `log_event` (names and keys are
  a contract with KPI-dashboard). Every feature handling user text has a
  non-leak test (`log_stream`).
- User-facing errors: a `ServiceError` subclass with a generic Italian message.
- Releases are fail-closed: nothing is downloadable unless verification passed.
- Test data is synthetic: the only codice fiscale is `RSSMRA85M01H501U`.

## Commands

From `backend/` (or `just backend::<recipe>` from the root):

```bash
just lint        # ruff fix + format + ty: before every commit
just test-cov    # unit tests, coverage at least 80%
just dev         # API with reload (needs the OCR server)
just evaluate    # scoring against ../dataset (needs OCR server and NER)
```
