# anonymization-pipeline

MIA platform service (AGENAS) for the **anonymization of clinical documents**
(evolution C1). A doctor uploads a referto, a discharge letter or a
prescription (native PDF, scanned PDF, photo); the service finds the personal
data and removes it: pages with personal data are replaced by an image of
themselves with black boxes painted in (layout, colours and tables look the
same, an invisible text layer keeps them searchable), the other pages stay as
they are. Every output is verified before it can be downloaded (fail-closed).
All models run on-prem, on open-source components with permissive licences
(pypdfium2, pypdf, LiteParse, RapidOCR with PP-OCRv5 models; no PyMuPDF). OCR runs in a second
service, `ocr-server/`; NER runs in the AILAB NER service.

Generated from `agenas-python-template`: same stack, logging and privacy rules
(see `CLAUDE.md` and `docs/`). The service is stateless: no database.

Two modes, chosen at upload (`POST /anonymizations?mode=`): `accurate` (default)
releases the redacted PDF, reviewable side by side and editable by hand; `fast`
releases only the anonymized text as Markdown, with page delimiters, tables
as Markdown tables and `[IMMAGINE]` placeholders.

How the algorithm works, the API, the PII taxonomy and the evaluation results:
**[docs/ANONYMIZATION.md](docs/ANONYMIZATION.md)**.

This branch holds the backend only: the engine, the API and the OCR server.
The demo page (`apps/web`) and the evaluation (`apps/eval`: synthetic dataset,
scoring, viewer) live on `ref/mod-separation`.

## How the algorithm works

```mermaid
flowchart TD
    IN["Uploaded document<br/>native PDF · scanned PDF · JPG/PNG/TIFF"] --> CONV["Convert to PDF<br/>images: EXIF rotation applied"]
    CONV --> PAGE{"For each page:<br/>image covers &gt; 50% of the page?"}

    PAGE -- "no: native page" --> NAT["Native text layer (pypdfium2)<br/>exact character boxes<br/>(invisible / white text and form fields included,<br/>rotated pages in unrotated coordinates)"]
    PAGE -- "yes: scanned page" --> OCR["OCR server (ocr-server/)<br/>PP-OCRv5 on RapidOCR, one box per word<br/>(margins re-read rotated 90°,<br/>pages and lines upside down re-read turned)"]
    PAGE -- "yes, with hidden OCR text" --> NAT

    NAT --> CELLS["Characters printed one per box joined<br/>R S S M R A … → RSSMRA…<br/>(on scans: boxes found from the ruling,<br/>read again cleaned of specks)"]
    OCR --> CELLS
    CELLS --> LAYERS[("Text layers<br/>text ↔ character boxes")]

    subgraph DET["PII detection (on text)"]
        RULES["Format rules<br/>tax code with checksum, health card, phones (IT, foreign, US),<br/>email, IBAN, NRE, dates, addresses, registrations, DICOM fields"]
        LABELS["Label → value (labels.yaml)<br/>Cognome: X · N. accettazione: Y · nato a Z"]
        NER["NER: AILAB NER service (POST /batch_ner)<br/>persons, places, addresses, organizations"]
        CLEAN["Person spans cleaned<br/>titles trimmed · 'la paziente' dropped ·<br/>institutions → organisation"]
        ALLOW["Eponym filter per occurrence (eponyms.yaml)<br/>segno di Giordano ≠ Sig. Giordano · drugs · saints"]
        PROP["Document-wide propagation<br/>every value found is searched everywhere: wrapped,<br/>glued, misread by OCR, same numbering scheme"]
        MERGE["Merge overlapping spans<br/>most specific label wins"]
        RULES --> CLEAN
        LABELS --> CLEAN
        NER --> CLEAN
        CLEAN --> ALLOW --> PROP --> MERGE
    end
    LAYERS --> DET

    subgraph VIS["Visual detection (on pixels and drawings)"]
        CODES["Barcodes and QR codes<br/>zxing"]
        IMGS["Embedded images<br/>signature / stamp (logos excluded)"]
        VEC["Vector drawings on native pages<br/>signature strokes, stamp frames"]
        INK["Ink not explained by OCR<br/>signatures on scans"]
        STAMP["Stamps: framed or coloured ink,<br/>or OMCeO / Medico Chirurgo text"]
    end
    PAGE --> VIS

    MERGE --> BOXES["Boxes to redact<br/>one per line · policy per label<br/>(clinical dates, age, facilities: kept)"]
    VIS --> BOXES
    BOXES --> EDIT["User edits (UI)<br/>draw area · un-redact box · search and redact"]
    EDIT --> RED

    subgraph RED["Redaction (pypdfium2 + pypdf)"]
        R1["Every occurrence of a redacted value<br/>gets a box (propagation)"]
        R2["Pages with boxes: rendered, boxes painted,<br/>replaced by the image + invisible text<br/>of the words left"]
        R3["Pages without boxes: copied as they are"]
        R4["File rebuilt from the pages alone: no metadata,<br/>XMP, annotations, attachments, JavaScript,<br/>bookmarks, structure tree, history"]
        R1 --> R2 --> R3 --> R4
    end

    RED --> VER{"Fail-closed verification<br/>· no text left under boxes<br/>· no value in text, bytes, metadata<br/>· pixels identical outside boxes"}
    VER -- passed --> OUT["Anonymized PDF (identical look)<br/>+ Word with the text"]
    VER -- "failed" --> FLAT["Values left: boxed where the check found them;<br/>failing pages without boxes: flattened too"]
    FLAT --> VER2{"Verified again"}
    VER2 -- passed --> OUT
    VER2 -- failed --> BLOCK["Document BLOCKED<br/>not downloadable, reasons shown"]
    BLOCK -.-> EDIT
```

1. **Text layers.** Every page becomes text in which each character keeps its
   box on the page: from the PDF text layer read by pypdfium2 on native pages
   (invisible text and form fields included), from the OCR server (PP-OCRv5) on
   scanned pages (side margins read again rotated by 90 degrees; pages and lines
   upside down read again turned by 180 degrees). Lines are rebuilt from
   geometry; characters printed one per box are joined into one word. On scans
   the boxes are found from the ruling of the form and read again cleaned of
   specks (recognition only, on the OCR server).
2. **Detection on the text.** Format rules (codice fiscale with checksum,
   tessera sanitaria, Italian and foreign phones, emails, IDs, dates,
   addresses, professional registrations), the value after a form label
   ("Cognome:", "N. accettazione") and the AILAB NER service (`ANONPIP_NER_URL`, fail-closed). An eponym filter decides each occurrence ("segno di Giordano"
   stays, "Sig. Giordano" goes); every value found is searched across the
   whole document, also when wrapped, glued to other words or misread by OCR.
3. **Detection on the pixels and drawings.** Barcodes and QR codes, signatures
   and stamps (embedded images, vector drawings, ink that OCR does not
   explain, framed or coloured stamps).
4. **Boxes.** One box per line of every finding; quasi-identifiers (clinical
   dates, age, facilities) are detected but kept by default. Through the API
   (`POST /{id}/edits`) the user can add an area, un-redact a box or search and redact a text.
5. **Redaction.** Every occurrence of a redacted value gets a box. Each page
   with a box is replaced by a rendering of itself with the boxes painted in
   (only its pixels survive) plus an invisible text layer of the words left;
   the other pages are copied; pypdf rebuilds the file from the pages alone
   (no metadata, annotations, attachments, bookmarks, history).
6. **Fail-closed verification.** Nothing readable under the boxes, no redacted
   value anywhere in the text, file objects or metadata, pixels identical
   outside the boxes. A value still found is boxed where the check found it and
   the document is checked again; if it still fails it is blocked and cannot be
   downloaded.

## Quick start

Requirements: `uv`, `just`. A GPU is optional (about 3 s per scanned page on a
laptop RTX 5070, slower on CPU; see "Speed" in [docs/ANONYMIZATION.md](docs/ANONYMIZATION.md)).

```bash
cp .env.example .env
just start          # deps, OCR server, API on the first free port, OpenAPI page in the browser
```

`just start` starts the OCR server (`ocr-server/`, port 8829) unless one is
already running, downloads the OCR weights on the first run (NER: set
`ANONPIP_NER_URL` to the AILAB NER service), starts the API on
`http://127.0.0.1:8000` (or the next free port) and opens its OpenAPI page
(`/docs`, off in prod) once `/readyz` is ready.
`just dev` needs the OCR server too: `just ocr-server`.

With Docker: `just up` starts the API and the OCR server (weights cached in the `models`
volume; uncomment the GPU blocks in `docker-compose.yml` and set
`OCR_EXTRA=gpu` to use an NVIDIA GPU).

## Commands

| Command | What it does |
|---|---|
| `just start` | Local run: OCR server and API, OpenAPI page in the browser |
| `just dev` | API on the host with reload |
| `just up` / `just down` / `just logs` | Compose stack (API, OCR server) |
| `just models` | Check the OCR server and the NER service (both must answer) |
| `just ocr-server [cpu]` / `just ocr-server-test` | OCR server on the host, port 8829 / its unit tests |
| `just lint` | ruff fix + format + ty: before every commit |
| `just check` | Same checks without fixes (what CI runs) |
| `just test` / `just test-cov` | Unit tests of every package / with coverage (at least 80% per package) |
| `just test-core` / `just test-api` | One suite only (arguments go to pytest) |
| `just audit` | Vulnerabilities in the dependencies |

## Modules

One folder per pod. `backend/` is the service pod: the API and the engine, two
packages built into one image. `ocr-server/` is the OCR pod, called by the
backend over HTTP. Each package has its own dependencies, tests and lint, so it
can be extracted on its own:

```mermaid
flowchart LR
    CLIENT["fe or any client"] -- "HTTP /anonymizations" --> API
    API["backend/api<br/>the service: jobs, SSE, limits, KPI events<br/>(image anonymization-pipeline)"] -- "Python import" --> CORE
    CORE["backend/core<br/>the engine (library)"] -- "HTTP, OCR_CONTRACT.md" --> OCR["ocr-server<br/>PP-OCRv5 on ONNX Runtime<br/>(image paddle-ocr-server)"]
    CORE -- "HTTP /batch_ner" --> NER["AILAB NER service"]
```

| Package | Depends on | Never imports | Tests |
|---|---|---|---|
| `backend/core` | OCR server and NER over HTTP | the service, any web framework | `just test-core` |
| `backend/api` | the engine | - | `just test-api` |
| `ocr-server` | - (own lock, outside the workspace) | - | `just ocr-server-test` |

The "never imports" column is enforced by ruff (`banned-api` in each
`pyproject.toml`). To extract the engine, copy its folder: see
[backend/core/README.md](backend/core/README.md).

## Layout

The backend packages form a uv workspace: one lock, each package with its own
dependencies, tests and lint config. The root is not a package: its dev group is
the developer environment (every member). The OCR server has its own lock.

```
backend/                pod 1, image anonymization-pipeline
  Dockerfile            builds api + core (context: the repository root)
  core/                 the engine, a library: no web framework, never imports the
                        service (banned in its pyproject); own tests, coverage and
                        lint config, so it can be extracted as is
    src/anonymization_core/
      config.py             PipelineConfig (plain values, no environment lookup)
      factory.py            build_pipeline, load_models
      errors.py             engine errors (the service maps them to HTTP)
      convert.py            upload (PDF or image) -> PDF
      document.py           releases (PDF + Word, Markdown), manual edits, page images
      reader.py, layers.py, geometry.py, pdf_pages.py, pdf_text.py, cells.py
                            text layers: pypdfium2 text, LiteParse + OCR server
      pipeline.py           analysis: text layers -> findings -> boxes
      release.py, verify.py flatten pages with boxes + pypdf rewrite, checks
      export_docx.py, export_markdown.py, taxonomy.py
      detect/               rules, labels (+ labels.yaml), ner, eponyms
                            (+ eponyms.yaml), allowlist, ensemble, visual
      testing.py            synthetic PDFs and fake engines (extra `testing`)
    tests/                  engine tests: `just test-core`
  api/                  the service: scripts/entrypoint.sh, own tests (`just test-api`)
    src/anonymization_pipeline/
      main.py               create_app(), lifespan (pipeline and job manager on app.state)
      config.py             Settings (pydantic-settings, prefix ANONPIP_), pipeline_config()
      errors.py             service errors, Italian messages, engine error mapping
      logging_setup.py      setup_logging, log_event, request id
      privacy.py            hashing/masking, user-text redaction, log filter
      api/health.py         /healthz, /readyz (models loaded), /version
      api/anonymization.py  upload, SSE progress, edits, previews, downloads
      services/jobs.py      job lifecycle around the engine: queue, SSE, expiry, limits, KPI events
ocr-server/             pod 2, image paddle-ocr-server: PP-OCRv5 (RapidOCR) behind the
                        LiteParse OCR API; own pyproject, lock and Dockerfile (ONNX
                        Runtime, CPU or GPU), outside the workspace: `just ocr-server-test`
docs/                   ANONYMIZATION.md, PDF_BACKEND_GAPS.md, logging and privacy,
                        deploy handover
scripts/                start.sh: OCR server and API on the host (`just start`)
```

## Results

Measured with the evaluation package (`apps/eval` on `ref/mod-separation`) on
the whole evaluation set (200 documents, 326
pages, each as native PDF, scan and scan with a hidden OCR layer: 600 PDFs;
20/10/2026). The current stack (LiteParse + OCR server, AILAB NER service, OCR
orientation and character boxes) has not been measured yet: the tables below
measure each part on its own branch.

LiteParse stack, NER still GLiNER + GLiNER2 in the pod (pypdfium2 + pypdf,
LiteParse, PP-OCRv5 server on RapidOCR), before the OCR orientation and
character-box changes:

| | native | scan | scan + OCR layer |
|---|---|---|---|
| **Recall** (box, required PII) | 0.942 | 0.892 | 0.937 |
| **Precision** (box) | 0.953 | 0.906 | 0.866 |
| **F1** (box) | 0.948 | 0.899 | 0.900 |
| Precision / recall / F1 (word) | 0.930 / 0.957 / 0.944 | 0.725 / 0.922 / 0.812 | 0.689 / 0.956 / 0.801 |
| **Accuracy** (word) | 0.985 | 0.945 | 0.939 |
| **AP@0.5** (PII, label-agnostic) | 0.652 | 0.588 | 0.500 |
| **mAP@0.5** (mean over PII types) | 0.666 | 0.562 | 0.541 |
| Hidden PII leaked (white text, metadata, OCR layers) | 0/324 | n/a | 0/324 |
| Eponyms wrongly redacted | 32/415 | 40/415 | 49/415 |
| Documents passing verification | 200/200 | 200/200 | 200/200 |
| Seconds per page (laptop RTX 5070, 8 GB) | 1.4 | 3.0 | 3.3 |

Against the former PyMuPDF + RapidOCR pipeline (16/10/2026) recall is 0.6, 2.3
and 0.7 points lower and scans are about 5 times faster; the gaps, their
suspected causes and the next steps are in
[docs/PDF_BACKEND_GAPS.md](docs/PDF_BACKEND_GAPS.md).

AILAB NER service with the OCR orientation and character-box changes, OCR still
PyMuPDF + PaddleOCR PP-OCRv5 in the pod (201 documents, 329 pages, 603 PDFs:
the orientation document `e056` added; 21/10/2026). In brackets the same stack
before those changes (20/10/2026):

| | native | scan | scan + OCR layer |
|---|---|---|---|
| **Recall** (box, required PII) | 0.940 (0.938) | 0.931 (0.909) | 0.942 (0.939) |
| **Precision** (box) | 0.960 (0.959) | 0.940 (0.928) | 0.893 (0.884) |
| **F1** (box) | 0.950 (0.948) | 0.935 (0.918) | 0.917 (0.911) |
| Precision / recall / F1 (word) | 0.947 / 0.950 / 0.948 | 0.791 / 0.947 / 0.862 | 0.743 / 0.956 / 0.836 |
| **Accuracy** (word) | 0.987 | 0.961 | 0.952 |
| **AP@0.5** (PII, label-agnostic) | 0.657 | 0.586 | 0.504 |
| **mAP@0.5** (mean over PII types) | 0.672 | 0.587 | 0.566 |
| Hidden PII leaked (white text, metadata, OCR layers) | 0/324 | n/a | 1/324 |
| Eponyms wrongly redacted | 53/416 | 75/416 | 102/416 |
| Documents passing verification | 201/201 | 201/201 | 201/201 |
| Seconds per page (laptop RTX 5090, 24 GB) | 0.6 | 3.0 | 3.9 |

On plain scans the OCR changes recover lines the orientation classifier used to
drop and codes in character boxes broken by specks (codice fiscale 93% to 98%,
tessera sanitaria 91% to 100%). Compared with the earlier in-pod GLiNER +
GLiNER2, the AILAB service has 0.5 to 1 point less recall: names it replaces as
eponyms, foreign names and fragments taken for places (details in
docs/ANONYMIZATION.md, "OCR choice" and "NER choice").
The current stack has not been measured as a whole yet: run the evaluation of
`ref/mod-separation` against it.

- **Box level** (scikit-learn): every required ground-truth box is found when
  redaction covers at least 75% of it; every redaction box touching no ground
  truth is a false positive; boxes on quasi-identifiers only are ignored.
- **Word level** (scikit-learn): every word of the page, PII or not, redacted
  or not; the only level with true negatives, hence the only meaningful accuracy.
- **AP / mAP@0.5** (torchmetrics, COCO protocol): detections ranked by
  confidence, IoU 0.5, quasi-identifiers and invisible text as ignore regions,
  duplicates collapsed by NMS (torchvision). AP is lower than recall on scans
  because it also scores localisation: the misaligned boxes of a scanner's
  hidden OCR layer count as duplicates, signatures are covered by an ink box
  smaller than the ground-truth image box, and address, CAP and city are
  annotated separately but detected as one box.

Recall is the safety metric (a missed value is a leak). Definitions, per-type
results and the analysis are in [docs/ANONYMIZATION.md](docs/ANONYMIZATION.md).

## Notes

- The API serves no page: in the platform the doctor-facing frontend is `fe`
  (a demo page for local tests is `apps/web` on `ref/mod-separation`).
- Licences: every component is open source; the licence audit and the few
  non-permissive transitive packages left are in
  [docs/PDF_BACKEND_GAPS.md](docs/PDF_BACKEND_GAPS.md).
- Deploy: `docs/DEPLOY_HANDOVER.md` lists image, variables, resources and the
  model weights the platform team has to provide (the cluster has no internet access).
