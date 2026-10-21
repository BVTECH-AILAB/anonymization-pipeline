# Open stack (pypdfium2, pypdf, LiteParse, RapidOCR): open gaps

Since 2026-10-20 the service runs only on open-source components: PyMuPDF
(AGPL) was removed. This page compares the current pipeline with the former
PyMuPDF + RapidOCR one and lists what is left to try. These gaps are left for a
later iteration.

| Role | Component | Licence |
|---|---|---|
| Native text, exact character boxes, rendering, flatten, visual detectors | pypdfium2 (PDFium) | BSD-3 / Apache-2.0 |
| Structure rebuild, scrub, raw verification | pypdf | BSD-3 |
| Scan detection, page screenshots for OCR | LiteParse (PDFium) | Apache-2.0 |
| OCR (`ocr-server/`) | RapidOCR on ONNX Runtime, PP-OCRv5 server detector + Latin recogniser | Apache-2.0, MIT; models Apache-2.0 |

## Measured on the 600-PDF gold set (2026-10-20)

Same NER, rules and evaluation for both. Box level, all documents; the test
split (77 documents never used for choices) moves the same way.

| | PyMuPDF + RapidOCR | open stack | delta |
|---|---|---|---|
| native: recall / precision | 0.948 / 0.970 | 0.947 / 0.961 | -0.1 / -0.9 pts |
| scan: recall / precision | 0.915 / 0.938 | 0.918 / 0.937 | +0.3 / -0.1 pts |
| scan_ocr: recall / precision | 0.944 / 0.904 | 0.938 / 0.895 | -0.6 / -0.9 pts |
| AP@0.5 native / scan / scan_ocr | 0.689 / 0.582 / 0.508 | 0.683 / 0.607 / 0.533 | |
| Boxes with no PII native / scan / scan_ocr | 195 / 406 / 671 | 256 / 416 / 742 | +61 / +10 / +71 |
| Eponyms wrongly redacted native / scan / scan_ocr | 23 / 44 / 50 of 415 | 30 / 46 / 55 of 415 | |
| Hidden PII leaked (native, scan_ocr) | 0/324, 0/324 | 0/324, 0/324 | = |
| Verified documents | 200/200 | 200/200 | = |

Speed: the two runs above are not comparable (the baseline had OCR and NER
fighting for the 8 GB laptop GPU, 15 s per scanned page; the open stack ran NER
on the CPU, 10.5 s). Since the OCR server releases GPU memory after every run
(peak about 4 GB instead of the whole card), OCR and NER share the GPU: on 12
documents per variant, 0.7 s per native page and 2.4-2.5 s per scanned page,
with results identical to the final run.

Redacted output: pages with boxes become images with an invisible text layer;
native documents grow by about 45%, scans shrink by about 35%.

## Gaps, largest first

### 1. Long identifiers on scans with a hidden OCR layer

| Type | baseline | open stack | total |
|---|---|---|---|
| FISCAL_CODE | 919 | 904 | 942 |
| MEDICAL_RECORD | 560 | 548 | 710 |
| ADDRESS | 268 | 264 | 287 |

On plain scans the same types are on par or better (HEALTH_CARD 354 -> 386 of
388). Next step: on the dev split, list the codes missed only on `scan_ocr`
and check whether the hidden layer and the OCR layer disagree on them
(propagation and overlap rules), or OCR misreads them.

### 2. Precision on native PDFs (about +60 boxes with no PII)

Mostly PERSON and PROFESSIONAL_ID (115 -> 112 of 127). Line building
(`reader.group_lines`) still joins or splits some table cells differently from
the former reader, which NER reads as names; eponyms follow (23 -> 30). Next
step: list the false positives present only with the open stack on the dev
split, grouped by cause, before changing `LINE_GAP`.

### 3. Writer

- **Pages with a box become images**: no vector text, larger native files. A
  per-object variant (remove text objects fully inside a box, blank image
  pixels) would keep more pages vector.
- **BARCODE on native PDFs: 70 -> 69 of 71** since the visual detectors use
  pypdfium2 renderings.
- **Propagation in the writer** (`cover_values`, `cover_found`) covers every
  occurrence of a redacted identifier or name, also where detection missed it
  (a phone glued to "numero", a URL wrapped next to its QR code, a fax header).
  Move it into the pipeline so the UI shows these boxes too.

## Behaviour that must stay (do not undo)

Each has a regression test in `packages/anonymization-core/tests/test_liteparse_reader.py`,
`packages/anonymization-core/tests/test_release.py` or `ocr-server/tests/`.

- **LiteParse never runs OCR itself.** With OCR on, it replaces a scan's hidden
  text layer with what OCR sees, and hidden values were never redacted (3
  leaks on `scan_ocr`). Scanned pages are read whole by the OCR server.
- **Native text from pypdfium2, built on the page as displayed**, form fields
  included: exact character boxes, tables on rotated pages read row by row.
- **Lines by neighbour chaining, in reading order** (`reader.group_lines`).
- **One- and two-character OCR results read left to right**: a digit is
  taller than wide and was taken for vertical text, which split codes printed
  one character per cell.
- **OCR on native pages only for grayscale clinical pictures**, and only the
  words inside them; codes, signatures and photos go to the visual detectors.
- **Flattened pages are drawn upright** in display space, so the pixel check
  holds on `/Rotate` pages; the invisible text keeps explicit spaces.

## Licences (checked 2026-10-20)

Installed packages of the service (runtime) and of `ocr-server/`, model
weights and the native libraries bundled in the wheels. Everything is MIT,
BSD, Apache-2.0, PSF or ISC except:

| Package | Licence | Pulled by | Status |
|---|---|---|---|
| text-unidecode | Artistic-1.0 or GPL-2.0+ | python-codicefiscale -> python-slugify | Replace python-codicefiscale (`detect/rules.py`, `anonymization_eval/dataset/identities.py`) |
| certifi, tqdm | MPL-2.0 | httpx, rapidocr | Weak copyleft, used unmodified: acceptable |
| nvidia-*, cuda-toolkit | NVIDIA proprietary (redistributable) | torch cu128 (dev only: the mAP of `apps/eval`, never in the service image), onnxruntime-gpu[cuda,cudnn] (OCR server) | Not open source; absent from the service image, a CPU OCR image drops them there |

Model weights (OCR server only, the NER model lives in the AILAB service), all
Apache-2.0: PP-OCRv5 server detector, Latin
PP-OCRv5 mobile recogniser, PP-OCR mobile text-direction classifier. PDFium in
pypdfium2 and LiteParse: BSD-3/Apache-2.0 with permissive third-party parts
(FreeType FTL, ICU, lcms, libjpeg-turbo, OpenJPEG, libpng, zlib, abseil), some
of which ask for a credit line in the product documentation.
