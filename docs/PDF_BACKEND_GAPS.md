# PDF backend (pypdfium2, pypdf, LiteParse, RapidOCR): components, open points, licences

The service runs only on open-source components with permissive licences.
This page lists them, the behaviour each part must keep, the open points
measured on the evaluation set and the licence audit.

| Role | Component | Licence |
|---|---|---|
| Native text, exact character boxes, rendering, flatten, visual detectors | pypdfium2 (PDFium) | BSD-3 / Apache-2.0 |
| Structure rebuild, scrub, raw verification | pypdf | BSD-3 |
| Scan detection, page screenshots for OCR, tables of the fast mode | LiteParse (PDFium) | Apache-2.0 |
| OCR (`ocr-server/`) | RapidOCR on ONNX Runtime, PP-OCRv5 server detector + Latin recogniser | Apache-2.0, MIT; models Apache-2.0 |

Redacted output: pages with boxes become images with an invisible text layer;
native documents grow by about 45%, scans shrink by about 35%.

## Open points, largest first

Numbers from the evaluation of 22/10/2026 (200 documents, 600 PDFs, box level;
`reports/evaluation.md` lists every miss).

### 1. Long identifiers on scans

| Type | native | scan | scan + OCR layer |
|---|---|---|---|
| FISCAL_CODE | 99% (932/942) | 94% (883/942) | 96% (900/942) |
| MEDICAL_RECORD | 79% (564/710) | 74% (528/710) | 76% (539/710) |
| ID_DOCUMENT | 91% (49/54) | 70% (38/54) | 91% (49/54) |

HEALTH_CARD is at 100% on every variant. Next step: on the dev split, list the
codes missed only on scans and check whether OCR misreads them (specks, faint
characters, dropout grids) or the format rules reject a reading that is one
character off.

### 2. Over-redaction

Boxes with no PII: 305 native, 494 scan, 922 scan with a hidden OCR layer;
eponyms wrongly redacted: 65, 77 and 108 of 415. Causes to check: on
`scan_ocr`, boxes of the scanner's hidden layer misaligned with the visible
text (real redactions of hidden text, counted as false positives); on every
variant, facility addresses and phones in the headers, table cells that NER
reads as names, eponyms. Next step: list the false positives on the dev split,
grouped by cause, before changing `LINE_GAP` in `reader.group_lines` or the
eponym lists.

### 3. Writer

- **Pages with a box become images**: no vector text, larger native files. A
  per-object variant (remove text objects fully inside a box, blank image
  pixels) would keep more pages vector.
- **BARCODE on native PDFs: 69 of 71**: both misses are on one document
  (`referto_radiologico_complessa_04`), while its scans are at 71 of 71; the
  cause in the rendering of the native page is still to find.
- **Propagation in the writer** (`cover_values`, `cover_found`) covers every
  occurrence of a redacted identifier or name, also where detection missed it
  (a phone glued to "numero", a URL wrapped next to its QR code, a fax header).
  Moving it into the pipeline would let the UI show these boxes too.

## Behaviour that must stay

Each has a regression test in `backend/core/tests/test_liteparse_reader.py`,
`backend/core/tests/test_release.py` or `ocr-server/tests/`.

- **LiteParse never runs OCR itself.** With OCR on, it would replace a scan's
  hidden text layer with what OCR sees, and hidden values would never be
  redacted. Scanned pages are read whole by the OCR server.
- **Native text from pypdfium2, built on the page as displayed**, form fields
  included: exact character boxes, tables on rotated pages read row by row.
- **Lines by neighbour chaining, in reading order** (`reader.group_lines`).
- **One- and two-character OCR results read left to right**: a digit is
  taller than wide and would be taken for vertical text, which would split
  codes printed one character per cell.
- **OCR on native pages only for grayscale clinical pictures**, and only the
  words inside them; codes, signatures and photos go to the visual detectors.
- **Flattened pages are drawn upright** in display space, so the pixel check
  holds on `/Rotate` pages; the invisible text keeps explicit spaces.

## Licences (checked 2026-10-20)

Installed packages of the service (runtime) and of `ocr-server/`, model
weights and the native libraries bundled in the wheels. The evaluation
(`evaluation/`, dev only) is never part of an image. Everything is MIT, BSD,
Apache-2.0, PSF or ISC except:

| Package | Licence | Pulled by | Status |
|---|---|---|---|
| text-unidecode | Artistic-1.0 or GPL-2.0+ | python-codicefiscale -> python-slugify | Replace python-codicefiscale (`detect/rules.py`) |
| certifi, tqdm | MPL-2.0 | httpx, rapidocr | Weak copyleft, used unmodified: acceptable |
| nvidia-*, cuda-toolkit | NVIDIA proprietary (redistributable) | onnxruntime-gpu[cuda,cudnn] (OCR server) | Not open source; absent from the service image, a CPU OCR image drops them there |

Model weights (OCR server only, the NER model lives in the AILAB service), all
Apache-2.0: PP-OCRv5 server detector, Latin
PP-OCRv5 mobile recogniser, PP-OCR mobile text-direction classifier. PDFium in
pypdfium2 and LiteParse: BSD-3/Apache-2.0 with permissive third-party parts
(FreeType FTL, ICU, lcms, libjpeg-turbo, OpenJPEG, libpng, zlib, abseil), some
of which ask for a credit line in the product documentation.
