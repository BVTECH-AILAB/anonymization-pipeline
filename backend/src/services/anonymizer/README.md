# The anonymization engine (`src/services/anonymizer`)

Surgical anonymization of clinical documents: PDFs (native or scanned) and
images in, a redacted PDF, a Word copy or a Markdown text out, each verified
before release. A sub-package of the service, like `docgen/` in ai-platform-v2,
kept self-contained: no web framework, no environment lookup, no logging setup,
and it never imports the service around it (router, session service, app,
`Settings`), as `ruff.toml` in this folder enforces. Its clients of external
services live in `src/llms/` (`ner.py`, `ocr.py`).

## What it needs at run time

| Dependency | Why | Contract |
|---|---|---|
| OCR server | Scanned pages and the grayscale pictures of native pages | [OCR_CONTRACT.md](OCR_CONTRACT.md), client `src/llms/ocr.py`; reference server `ocr-server/` |
| NER service (AILAB, `POST /batch_ner`) | Names, places, organisations in free text | `src/llms/ner.py`; optional (`ner_enabled=False`) |

Both are reached over HTTP. Until they answer, `load_models` returns False and
nothing is read (fail-closed). A NER failure while reading raises
`NerUnavailableError` and nothing is released.

## Use

```python
from pathlib import Path

from src.services.anonymizer.config import PipelineConfig
from src.services.anonymizer.convert import upload_to_pdf
from src.services.anonymizer.document import release_markdown, release_pdf
from src.services.anonymizer.factory import build_pipeline, load_models

config = PipelineConfig(
    ocr_server_url="http://127.0.0.1:8829",
    ner_url="http://ner.example:8080",
    redaction_policy={"AGE": True},  # quasi-identifiers are kept by default
)
pipeline = build_pipeline(config)
if not load_models(pipeline):  # checks the OCR server and the NER service
    raise SystemExit("OCR server or NER service not ready")

pages = upload_to_pdf(
    Path("scan.jpg").read_bytes(), ".jpg", Path("original.pdf"), max_pages=50
)
analysis = pipeline.analyze(Path("original.pdf"))

pdf = release_pdf(
    Path("original.pdf"),
    analysis,
    Path("anonymized.pdf"),
    Path("anonymized.docx"),
    style="black",
    tol=2e-4,
)
if not pdf.verified:  # never hand out a file that failed verification
    raise SystemExit(pdf.issues)

text = release_markdown(
    pipeline.analyze(Path("original.pdf"), visual=False), Path("anonymized.md")
)
```

| Module | What it holds |
|---|---|
| `config.py` | `PipelineConfig`: OCR, NER, thresholds, redaction policy |
| `factory.py` | `build_pipeline`, `load_models` |
| `errors.py` | Engine errors (`AnonymizationError` and subclasses): map them to your own responses |
| `convert.py` | `upload_to_pdf`: PDF checks, images to one page per frame, size and page limits |
| `pipeline.py` | `Pipeline.analyze` -> `Analysis` (boxes, counts, `search`, `snap`) |
| `document.py` | `release_pdf`, `release_markdown`, `apply_edits`, `display_boxes`, `render_png` |
| `detect/` | Rules, labels, eponyms, allowlist, ensemble, visual detectors |
| `release.py`, `verify.py` | Redaction (flattened pages, pypdf rewrite) and the checks before release |

PDFium is not thread-safe: every entry point takes `pdf_pages.PDFIUM_LOCK`
itself, so callers can use threads.

## Privacy

The engine logs ids, counters and exception types only, never document text,
values or file names. Its tests capture every log record at DEBUG without any
redaction filter and assert that no PII appears. Callers still install their
own filter (the service does).

## Development

From `backend/`:

```bash
uv run pytest tests/services/anonymizer tests/llms   # the engine and its clients
just lint                                            # ruff (boundaries included) and ty
```

The test kit (synthetic PDFs, fake reader and NER, `OcrServerStub`, PDFium lock
check) is `tests/documents.py`.

## Extracting it

Copy `src/services/anonymizer/`, `src/llms/`, `tests/services/anonymizer/`,
`tests/llms/` and `tests/documents.py`, take the engine section of the
dependencies in `backend/pyproject.toml`, and rewrite the `src.` prefix of the
imports. Nothing in the engine refers to the rest of the service.
