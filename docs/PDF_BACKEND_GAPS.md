# Open stack (LiteParse, PaddleOCR, pypdfium2, pypdf): open gaps

Since 2026-10-20 the service runs only on open-source components with
permissive licences: PyMuPDF (AGPL) and RapidOCR were removed. This page
compares the current pipeline with the former PyMuPDF + RapidOCR one, measured
before the removal, and lists what to try next. These gaps are left for a later
iteration.

## Measured on the 600-PDF gold set (2026-10-20)

Same NER, rules, redaction and verification for both; only the text layers and
the OCR differ. Laptop RTX 5070 8 GB. The comparison was made with the two
backends side by side, before PyMuPDF was removed; `just eval-compare a b`
compares any two report folders.

| | PyMuPDF + RapidOCR | LiteParse + PaddleOCR | delta |
|---|---|---|---|
| native: recall / precision (box) | 0.948 / 0.970 | 0.942 / 0.954 | -0.6 / -1.6 pts |
| scan: recall / precision (box) | 0.915 / 0.938 | 0.890 / 0.906 | -2.5 / -3.2 pts |
| scan_ocr: recall / precision (box) | 0.944 / 0.904 | 0.936 / 0.867 | -0.8 / -3.7 pts |
| AP@0.5 native / scan / scan_ocr | 0.689 / 0.582 / 0.508 | 0.653 / 0.589 / 0.507 | |
| Boxes with no PII native / scan / scan_ocr | 195 / 406 / 671 | 308 / 624 / 966 | +113 / +218 / +295 |
| Hidden PII leaked (native, scan_ocr) | 0/324, 0/324 | 0/324, 0/324 | = |
| Verified documents | 200/200 | 200/200 | = |
| Seconds per page native / scan / scan_ocr | 1.3 / 15.2 / 15.6 | 1.3 / 3.0 / 3.6 | |

The baseline ran at 15 s per scanned page, twice the 7.6 s measured on
16/10/2026: rerun it on an idle GPU before quoting the speed-up.

## Phase B: redaction without PyMuPDF (2026-10-20)

The pypdfium2 + pypdf writer with the LiteParse reader; visual detectors,
uploads, previews and the Word export on pypdfium2. Same gold set:

| | liteparse + PyMuPDF writer | liteparse + pdfium writer |
|---|---|---|
| native: recall / precision | 0.942 / 0.954 | 0.942 / 0.953 |
| scan: recall / precision | 0.890 / 0.906 | 0.892 / 0.906 |
| scan_ocr: recall / precision | 0.936 / 0.867 | 0.937 / 0.866 |
| Hidden PII leaked | 0/324, 0/324 | 0/324, 0/324 |
| Verified documents | 200/200 | 200/200 |
| Seconds per page native / scan / scan_ocr | 1.26 / 3.01 / 3.58 | 1.41 / 2.96 / 3.27 |
| Output size native / scan / scan_ocr | 88 MB / 1.2 GB / 1.2 GB (PyMuPDF baseline) | 128 MB / 783 MB / 775 MB |

Open points of the writer:
- **Pages with a box become images**: no vector text, about +45% size on native
  documents; the invisible text layer keeps them searchable. A per-object
  variant (remove text objects fully inside a box, blank image pixels) would
  keep more pages vector.
- **BARCODE on native PDFs: 70 -> 69 of 71** after moving the visual detectors
  to pypdfium2 renderings; find the code and compare the two renderings.
- **Propagation** (`cover_values`, `cover_found`): every occurrence of a
  redacted identifier or full name is covered, also where detection missed it.
  It found misses the PyMuPDF writer cannot see on scans (its output has no text
  to check, the value stays in the pixels): a phone glued to "numero", a URL
  wrapped next to its QR code, a fax header. Move it into the pipeline so both
  writers and the UI show these boxes.

## Gaps, largest first

### 1. Long identifiers on scans

Values found by PyMuPDF + RapidOCR and missed by LiteParse + PaddleOCR:

| Variant | Type | baseline | liteparse | total |
|---|---|---|---|---|
| scan | FISCAL_CODE | 879 | 805 | 942 |
| scan | MEDICAL_RECORD | 544 | 498 | 710 |
| scan | ID_DOCUMENT | 42 | 32 | 54 |
| scan | PERSON | 2408 | 2379 | 2518 |
| scan_ocr | MEDICAL_RECORD | 560 | 536 | 710 |

Suspected cause: the OCR server caps the long side of the image the detector
sees at 2400 px (`OCRSRV_DET_LIMIT_SIDE_LEN`); 300 dpi pages are 3508 px.
Without the cap the detector takes about 20 s per page. RapidOCR runs the same
PP-OCRv5 server detector without that cap.

Next steps:
- On the **dev** split only (`--split dev`), try 3000 and 3508 and measure
  recall and seconds per page; keep the test split for the final check.
- List the missed codes of the dev split and check if they are recognition
  errors (wrong characters) or detection errors (no box): the remedy differs.

### 2. Over-redaction: about one extra box with no PII per page

All variants, mostly PERSON, LOCATION and ADDRESS. Causes found so far:
- Word boxes: LiteParse gives one box per word, PyMuPDF one per character.
  Character boxes are interpolated inside the word, so a span covering part of
  a word (a value glued to its label) gets a box that is slightly off.
- Line building (`reader.group_lines`) agrees with PyMuPDF on 99.3% of word
  pairs and keeps the reading order on 98.8%: the rest still joins or splits
  some table cells, which NER reads as names.

Next steps: list the false positives present only with LiteParse on the dev
split, grouped by cause, before changing `LINE_GAP` (0.8 word heights,
measured on the native gold set).

### 3. Lower AP on native PDFs (0.689 -> 0.653)

Box geometry, not detection: LiteParse word boxes are a little taller than
PyMuPDF character boxes. Recall is almost the same (-0.6 pts). It matters only
if AP becomes an acceptance metric.

### 4. Eponyms on native PDFs (23 -> 32 of 415 wrongly redacted)

Same root as gap 2: lines that join cells give NER a name-like context.

## Behaviour that must stay (do not undo)

These were bugs found while closing the gap; each has a regression test in
`tests/unit/test_liteparse_reader.py` or `ocr-server/tests/`.

- **Native text is always read with OCR off.** With OCR on, LiteParse replaces
  a scan's hidden text layer with what OCR sees, and hidden values were never
  redacted (3 leaks on `scan_ocr`).
- **Scanned pages are OCR'd whole by the server**, as the PyMuPDF backend does;
  LiteParse's own OCR is used only for pictures on native pages.
- **Word boxes widened to whole characters** (`ocr-server`, `words.widen`):
  PaddleX boxes span CTC columns, a single digit got a 1.4 pt box and was left
  partly visible.
- **Vertical native text regrouped** (`reader.regroup_rotated`): LiteParse
  splits words written at 90 degrees ("RS SMRA85M01H501U").
- **Lines by neighbour chaining, in reading order** (`reader.group_lines`).
- **One- and two-character OCR results read left to right**: a digit is
  taller than wide and was taken for vertical text, which split codes printed
  one character per cell.

## Licences of the stack (checked 2026-10-20)

Installed packages of the service (runtime only) and of `ocr-server/`, model
weights and the native libraries bundled in the wheels. Everything is MIT, BSD,
Apache-2.0, PSF or ISC except:

| Package | Licence | Pulled by | Status |
|---|---|---|---|
| pymupdf | AGPL-3.0 (or Artifex commercial) | was direct | **Removed** on 2026-10-20 (with rapidocr and onnxruntime) |
| text-unidecode | Artistic-1.0 or GPL-2.0+ | python-codicefiscale -> python-slugify | Replace python-codicefiscale (`detect/rules.py`, `tools/dataset/identities.py`) |
| python-bidi | LGPL-3.0 | paddlex[ocr-core] (`ocr-server`) | Not needed for inference: exclude, or run the server on RapidOCR |
| crc32c | LGPL-2.1+ | paddlex -> aistudio-sdk -> bce-python-sdk (`ocr-server`) | Model download client, not needed for inference |
| certifi, tqdm | MPL-2.0 | httpx, huggingface-hub, gliner | Weak copyleft, used unmodified: acceptable |
| nvidia-* , cuda-python | NVIDIA proprietary (redistributable) | torch cu128, paddlepaddle-gpu | Not open source; a CPU image drops them |

Model weights, all Apache-2.0: `urchade/gliner_multi_pii-v1`,
`fastino/gliner2-privacy-filter-PII-multi`, `PP-OCRv5_server_det`,
`latin_PP-OCRv5_mobile_rec`. PDFium in pypdfium2 and LiteParse: BSD-3/Apache-2.0
with permissive third-party parts (FreeType FTL, ICU, lcms, libjpeg-turbo,
OpenJPEG, libpng, zlib, abseil).
