# anonymization-core

Surgical anonymization of clinical documents: PDFs (native or scanned) and
images in, a redacted PDF, a Word copy or a Markdown text out, each verified
before release. A library: no web framework, no environment lookup, no
logging setup. The service (`apps/api`) and the evaluation (`apps/eval`) are
built on it, and it imports neither (banned in `pyproject.toml`).

## What it needs at run time

| Dependency | Why | Contract |
|---|---|---|
| OCR server | Scanned pages and the grayscale pictures of native pages | [OCR_CONTRACT.md](OCR_CONTRACT.md); reference server `services/ocr-server` |
| NER service (AILAB, `POST /batch_ner`) | Names, places, organisations in free text | `detect/ner.py`; optional (`ner_enabled=False`) |

Both are reached over HTTP. Until they answer, `load_models` returns False and
nothing is read (fail-closed). A NER failure while reading raises
`NerUnavailableError` and nothing is released.

## Use

```python
from pathlib import Path

from anonymization_core.config import PipelineConfig
from anonymization_core.convert import upload_to_pdf
from anonymization_core.document import release_markdown, release_pdf
from anonymization_core.factory import build_pipeline, load_models

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
| `detect/` | Rules, labels, NER client, eponyms, allowlist, ensemble, visual detectors |
| `release.py`, `verify.py` | Redaction (flattened pages, pypdf rewrite) and the checks before release |
| `testing.py` | Synthetic PDFs, fake reader and NER, `OcrServerStub`, PDFium lock check (extra `testing`) |

PDFium is not thread-safe: every entry point takes `pdf_pages.PDFIUM_LOCK`
itself, so callers can use threads.

## Privacy

The engine logs ids, counters and exception types only, never document text,
values or file names. Its tests capture every log record at DEBUG without any
redaction filter and assert that no PII appears. Callers still install their
own filter (the service does).

## Development

```bash
uv sync --package anonymization-core   # inside the workspace
uv run pytest                          # from this folder: 80% coverage gate
uv run ruff check && uv run ty check src tests
```

## Extracting it

The folder is self-contained: dependencies, test kit, lint, type and coverage
settings, tests.

1. Copy `packages/anonymization-core/` to the new repository.
2. Run `uv lock`: the folder carries no lock of its own.
3. Run `uv sync && uv run pytest`.
4. In each consumer, replace `anonymization-core = { workspace = true }` in
   `[tool.uv.sources]` with the new source: a git URL, a path or a package
   index.

Nothing else refers to the workspace.
