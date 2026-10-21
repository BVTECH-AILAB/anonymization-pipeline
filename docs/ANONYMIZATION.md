# Anonymization of clinical documents

This service implements chapter **C1 · Anonimizzazione** of the MIA AI evolutions:
a doctor uploads a document (native PDF, scanned PDF, phone photo), the service
finds the personal data and removes it from the file. Pages with personal data
are replaced by an image of themselves with black boxes painted in (layout,
colours and tables look identical; an invisible text layer keeps them
searchable); pages without it stay as they are. Every output is verified before
it can be downloaded (fail-closed). Everything runs on-prem, on open-source
components with permissive licences (no PyMuPDF): the other services are
the OCR server of `services/ocr-server/`, deployed next to this one, and the AILAB NER
service.

## How it works

```mermaid
flowchart TD
    IN["Uploaded document<br/>native PDF · scanned PDF · JPG/PNG/TIFF"] --> CONV["Convert to PDF<br/>images: EXIF rotation applied"]
    CONV --> PAGE{"For each page:<br/>image covers &gt; 50% of the page?"}

    PAGE -- "no: native page" --> NAT["Native text layer (pypdfium2)<br/>exact character boxes<br/>(invisible / white text and form fields included,<br/>rotated pages in unrotated coordinates)"]
    PAGE -- "yes: scanned page" --> OCR["OCR server (services/ocr-server/)<br/>PP-OCRv5 on RapidOCR, one box per word<br/>(margins re-read rotated 90°,<br/>pages and lines upside down re-read turned)"]
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

### 1. Text layers (`anonymization_core/reader.py`, `layers.py`)

Detection runs on plain text; redaction runs on the PDF. The link is the
**text layer**: page text in which every character keeps its box on the page.
A detected span `(start, end)` becomes one rectangle per line.

- **Native pages**: pypdfium2 (PDFium) reads the PDF text layer with one exact
  box per character, from the font descender to its ascender. Invisible text
  (white, render mode 3) is included because it must be redacted too, and so
  are form-field values and visible annotations (read from a flattened copy).
  Words and lines are built on the page as displayed, so a table on a rotated
  page reads row by row. Words glued by a scanner's OCR layer are split where a
  capitalised word starts inside them ("prevpt:LindaMarshRX-550-2877" ->
  "Linda", "Marsh", "RX-550-2877"; "McDonald", "HbA1c", "SerD" stay whole).
- **Scanned pages** (a full-page image, or images over half of the page, as
  LiteParse classifies them): read whole by the OCR server of `services/ocr-server/`,
  PP-OCRv5 on RapidOCR (server detector, Latin recogniser, 300 DPI) behind the
  LiteParse OCR API. It returns one box per word; vertical text in the side
  margins (print traces, protocol numbers, wristband labels) is read by a
  second pass on the margin strips rotated by 90 degrees.
- **Orientation** (`services/ocr-server/`, `engine.py`): RapidOCR's line orientation
  classifier is off. It turned upright lines by 180 degrees, which were then
  read as noise and dropped whole, personal data included (see "OCR choice").
  Instead:
  - a page read with a mean confidence below 0.8 per character is read again
    turned by 180 degrees (a sheet fed upside down); the turned reading is kept
    only when it is both confident (0.8) and richer, its boxes mapped back with
    the reading direction right to left;
  - on an upright page, a line read below 0.5 is noise or text upside down (a
    patient label pasted the wrong way). The detector cuts such a line into
    pieces, so each low line is grouped with the unsure pieces next to it on its
    band (below 0.8) and the group is read again turned, as one crop
    (recognition only): a reading of at least 0.8 takes its place, otherwise the
    low lines are dropped (as RapidOCR did) and the unsure pieces kept. Lines
    read with confidence are never touched.
- A scan that also carries a hidden OCR layer gets both layers: the hidden text
  is redacted where it overlaps the findings.
- Lines are rebuilt from geometry (`group_lines`): each word is chained to its
  nearest neighbour on the same line, and lines are ordered in reading order,
  so table cells stay apart and a label comes before its value.
- Characters printed one per box (codice fiscale or tessera sanitaria on forms,
  "R S S M R A ...") are joined into one word whose character boxes are the
  cells (`merge_cells`), on native and OCR layers, so the rules see the code.
  Only capitals and digits are joined: the single-letter words of prose stay apart.
- **Character boxes on scans** (`cells.py`, OpenCV). Read as a page, every box
  is a separate word, and scanner specks inside the boxes come back as ".C",
  "i7", "[1" or a lower-case "s", which break the run. The boxes are found from
  the ruling of the form: adaptive threshold, a morphological opening with a
  30 pt horizontal element keeps the ruling lines, and between two lines 7 to
  30 pt apart the columns inked over 80% of the height are the borders; a row
  is at least 5 evenly sized boxes (0.4 to 1.8 times as wide as high). A row
  more than 35% inked inside is a title bar, not boxes. Boxes whose border was
  not seen are added along the ruling while they hold a character, snapped to
  the borders seen, with at most one box per side lacking its border (a faint
  outer border lost on the scan); past it the ruling runs over a table or text. Each
  box is cleaned (Otsu threshold, connected components: only strokes at least a
  quarter of the box high are kept) and the characters are set side by side,
  3 pt apart, into one word that the recogniser reads far better than isolated
  characters ("1" alone comes back as "(i:", "99" without the gap as "9"). The
  reading replaces the page OCR of the row only when it has one capital or digit
  per box with a confidence of at least 0.5, when the row holds the whole code
  (no word of the page OCR runs out of it, no single character is read right
  next to it: a row seen in part is left alone) and when the page OCR did not
  already read a valid code there (codice fiscale, tessera sanitaria, STP/ENI)
  that the clean reading is not. The composed word is read by the OCR server
  (`POST /recognize`, recognition only); boxes are read again only on scanned
  pages read upright.
- Grayscale pictures embedded in a native page (X-ray, CT, ultrasound, an
  embedded scan) stay visible; the text burned into them (patient name, ID,
  date of birth, operator, accession number) is read by the same server, only
  the words inside the picture are kept, and they are redacted like on a scan. Colour
  photos may show a person and are redacted whole (FACE).

The visual detectors work on pypdfium2 page views (`pdf_pages.py`). Measured
against the former PyMuPDF + RapidOCR pipeline, and what is left to do:
[PDF_BACKEND_GAPS.md](PDF_BACKEND_GAPS.md).

### 2. Detection (`anonymization_core/detect/`)

Recall first: one detector is enough to redact.

| Detector | What it finds |
|---|---|
| `rules.py` | Formats: codice fiscale (strict with checksum, plus an OCR-tolerant pattern), tessera sanitaria, phones (Italian, foreign "+234 ...", North American, extensions; never across lines, dates excluded), email, IBAN, URLs with personal tokens, dates (date of birth from context, also ISO, compact and "30-Nov-1966"), age, street addresses, CAP + municipality, professional registrations ("OMCeO MI n.", "Ord. Med. RN n.", "Albo PE n."), NRE, DICOM header fields ("(0010,0010) PatientName", also with the keyword split into words), places and names in typical phrasings |
| `labels.py` + `labels.yaml` | The value after a label: about 60 groups of Italian and English form and report labels ("Cognome:", "COGNOME / SURNAME" with the value below, "N. accettazione", "Prenotazione CUP", "Medicaid ID", "Permesso di soggiorno", "Medico curante", "Mediatore linguistico") with the shape of the value (name, place, address, organisation, identifier, date, phone). A single word after a generic role label ("Medico Ostetrico") needs ":" or capitals. A label with no value on its line takes the next line only if that line is not a sentence ("Mediatore linguistico:" followed by "Acconsento al test HIV" is not a name). An address needs a street type, "c/o" or "presso", a number, or the shape of a place name ("San Giovanni in Fiore"), so prose after "domicilio" is not one. Vocabulary ported from feat/reconstructed-architecture |
| `ner.py` | Client of the AILAB NER service (`POST /batch_ner`, one request per page, chunks of whole lines). Entity groups mapped to the taxonomy (NOME/COGNOME -> PERSON, LUOGO, INDIRIZZO, RAGIONE_SOCIALE ...), the others ignored; adjacent name and surname joined. The service preprocesses the text (eponyms become `EPONYMn`, emails a placeholder), so its offsets drift: word pieces are joined first ("Mo" + "##cenigo"), then each entity is checked against the original text and moved to the nearest occurrence of its word, or dropped. The service reads line breaks as spaces: a span is cut where the next line starts another field ("Grippo" + "Visto:", a stop word of `labels.yaml`, punctuation), while a value wrapped on two lines stays whole. Timeouts, connection errors and 502/503/504 are retried (`ANONPIP_NER_RETRIES`, waits doubling from `ANONPIP_NER_RETRY_BACKOFF_SECONDS`); then fail-closed: a service down fails the job. See "NER choice" |
| `eponyms.py` + `eponyms.yaml`, `allowlist.py` | Per occurrence: eponyms and scales are not persons only in a clinical context ("segno di Giordano negativo", "Glasgow 15", "Wolff-Parkinson-White"), drug brands never are, common words only in lowercase ("globuli rossi"); "Sig. Giordano" stays a person. About 400 eponyms, 50 scales, 200 drug brands. Plus saints in facility names and clinical vocabulary |
| `ensemble.py` | Person spans lose leading titles, references without a name ("la paziente") are dropped, institution names become organisations. Document-wide propagation: every name token and every redacted identifier found anywhere is searched in every layer (spaced, glued, with initials, both orders, wrapped on two lines); words ambiguous on their own (three letters or fewer, acronyms, field labels, eponyms, scales, Italian municipalities: "Ugo", "HIV", "Masson", "Napoli") are spread only as part of the full name, never alone. Also found: a surname glued to an initial in capitals ("TOKAFOR"), a full name glued to what follows ("LindaMarshRX-550"), OCR confusions 0/o, 1/l, 5/s in names and numbers ("Adebay0", "389 555 773l"), and other codes of the numbering scheme of a found identifier ("RX-550-2913" -> "RX-550-2877"). Names and places with stray symbols or numbers-only words are OCR fragments; addresses from NER need the shape of an address; facility names are organisations. Overlapping spans merge into the most specific label |
| `visual.py` | Barcodes and QR codes (zxing), embedded images (signature or stamp; grayscale clinical pictures left to OCR, colour photos redacted whole; header logos excluded), vector signatures and stamp frames on native pages, on scans the ink that OCR does not explain (signatures), stamps (frame, coloured ink, or a cluster of words around "OMCeO" / "Medico Chirurgo") |

### 3. PII taxonomy (`anonymization_core/taxonomy.py`)

| Label | Kind | Redacted by default |
|---|---|---|
| PERSON, FISCAL_CODE, HEALTH_CARD, ID_DOCUMENT, DATE_OF_BIRTH | text | yes |
| ADDRESS, LOCATION, PHONE, EMAIL | text | yes |
| MEDICAL_RECORD, PRESCRIPTION_ID, PROFESSIONAL_ID, IBAN, URL | text | yes |
| AGE, DATE (clinical dates), ORGANIZATION | text | no (quasi-identifiers, configurable) |
| SIGNATURE, STAMP, BARCODE, QRCODE, FACE, HANDWRITING | visual | yes |

What each label covers (detectors and reviewers apply the same definition):

| Label | Covers |
|---|---|
| `PERSON` | Names of patient, relatives, caregivers, doctors and staff, in any form (BIANCHI GIOVANNI, Bianchi G., Sig. Bianchi, Dott.ssa Ferri). Not eponyms (morbo di Crohn). |
| `FISCAL_CODE` | Codice fiscale of any person (patient or doctor), 16 characters with check char. |
| `HEALTH_CARD` | Tessera sanitaria / TEAM number (20 digits, starts with 80380). |
| `ID_DOCUMENT` | Identity card, passport, driving licence, permit numbers. |
| `DATE_OF_BIRTH` | Date of birth of a person. |
| `AGE` | Age; identifying above 89. |
| `ADDRESS` | Street address, house number, postcode of a person. |
| `LOCATION` | Birthplace, city/province of residence of a person. |
| `PHONE` | Phone, mobile and fax numbers of persons. |
| `EMAIL` | Email and PEC addresses of persons. |
| `MEDICAL_RECORD` | Nosologico, cartella clinica, MRN, accession number, sample ID, episode/admission number, report number. |
| `PRESCRIPTION_ID` | NRE (numero ricetta elettronica), ricetta number. |
| `PROFESSIONAL_ID` | OMCeO registration number, regional doctor code, doctor's CF. |
| `IBAN` | Bank account numbers. |
| `URL` | URLs carrying personal tokens (online report download links). |
| `ORGANIZATION` | Hospital, ward, clinic names (quasi-identifier). |
| `DATE` | Clinical dates: admission, discharge, exam (quasi-identifier). |
| `SIGNATURE` | Handwritten or scanned signatures. |
| `STAMP` | Doctor or facility stamps (timbri), which often contain names and IDs. |
| `BARCODE` | 1D barcodes (Code39/128 on prescriptions: CF, NRE). |
| `QRCODE` | 2D codes (QR, DataMatrix) that may encode personal data or links. |
| `FACE` | Photos of the patient's face. |
| `HANDWRITING` | Handwritten notes. |

### 4. Redaction (`anonymization_core/release.py`)

- **Propagation**: every occurrence of a redacted identifier or full name gets a
  box, also where detection missed it (glued to a label, broken by OCR, in a
  fax header), matched as the output check matches it (whitespace ignored).
  Occurrences the user chose to keep stay visible.
- **Pages with a box** are rendered (pypdfium2, 200 dpi, lossless) as they are
  displayed, the boxes painted in (`ANONPIP_REDACTION_STYLE=label` writes the
  PII type instead of a black box), and replaced by that image on an upright
  page: nothing of their content survives but pixels. An invisible text layer
  with the words that touch no box keeps them searchable.
- **Pages without boxes** are copied as they are.
- **pypdf rebuilds the file from the pages alone**: a fresh catalog, so no
  metadata, XMP, bookmarks, structure tree, named destinations, forms,
  attachments, JavaScript, annotations, page extras or earlier incremental
  versions (a note added and deleted, for example).
- Hand-drawn areas are snapped to the full box of every character they touch.

### 5. Verification (`anonymization_core/verify.py`)

A document is released only if all checks pass:

1. no original text is readable under any box (horizontal and vertical text);
2. no text is left outside the visible page area (it would never be detected
   nor shown, but it would stay in the file);
3. no redacted value (codici fiscali, phones, emails, IDs, full names...) is
   found in the output text, in the decompressed file bytes or in the metadata;
   occurrences the user explicitly chose to keep are not leaks;
4. no annotation is left;
5. rendered pages are identical outside the boxes (more than
   `ANONPIP_VERIFY_PIXEL_TOLERANCE` changed pixels fails; on rotated pages the
   boxes are turned to the rendering first);
6. the Word export contains none of the redacted values either.

**Second pass** (`release.py`). When a check fails, a value still found in the
output text is boxed where the check found it (the words of the output that
spell it), a page without boxes that failed is flattened too, and the file is
rebuilt and verified again; flattened pages are compared with the original as
images at 200 dpi. The result reports which pages were flattened
(`flattened_pages`).

A document that still fails is blocked (409 `VERIFICATION_FAILED`) and the
reasons are shown to the uploader. An edit marks the job unverified at once: nothing
is downloadable until the new version has passed every check (a version that
fails stays blocked; a failed edit leaves the job blocked and says so).

Coordinates: text, image positions and drawings live in page space (unrotated);
anything read from a rendering (OCR words, barcodes, ink) is in display space
and is turned into page space before detection and redaction, so `/Rotate`
pages are redacted where the text really is.

## API

Two modes, chosen at upload with `?mode=` (default `accurate`):

- **`accurate`**: the whole pipeline above; releases the redacted PDF and a Word
  copy, and supports the side-by-side review with manual edits.
- **`fast`**: the same text layers and text detection (rules, labels, NER,
  propagation), without the visual detectors, the redaction of the PDF and its
  pixel checks; releases only the anonymized text as Markdown (see "Fast mode").

| Method and path | What it does | Modes |
|---|---|---|
| `POST /anonymizations?mode=accurate\|fast` (multipart `file`) | Upload and start; 202 with `{id, pages, size, mode}` | both |
| `GET /anonymizations/{id}` | State; includes the result when done | both |
| `GET /anonymizations/{id}/events` | Server-Sent Events: one message per progress change, then the final state | both |
| `POST /anonymizations/{id}/cancel` | Stop at the next step; files deleted | both |
| `GET /anonymizations/{id}/preview` | Manifest of the side-by-side review (see below) | accurate |
| `GET /anonymizations/{id}/pages/original/{page}?dpi=` | PNG of the original page | both |
| `GET /anonymizations/{id}/pages/anonymized/{page}?dpi=` | PNG of the anonymized page | accurate |
| `POST /anonymizations/{id}/edits` | `{add: [{page, rect}], remove: [box ids], restore: [box ids], search: "text"}`: redaction and verification only, no new detection | accurate |
| `GET /anonymizations/{id}/download/{pdf\|docx}` | Anonymized document (refused when not verified) | accurate |
| `GET /anonymizations/{id}/download/md` | Anonymized text as Markdown (refused when not verified) | fast |
| `DELETE /anonymizations/{id}` | Delete now | both |
| `GET /anonymizations/labels` | Taxonomy with Italian names | - |

An action of the other mode answers 409 `MODE_NOT_SUPPORTED`.

### Side-by-side review (frontend contract)

1. `POST /anonymizations?mode=accurate`, then follow `/events` (or poll `GET /{id}`)
   until `status` is `done`.
2. `GET /{id}/preview`:

   ```json
   {
     "id": "<32 hex>", "mode": "accurate", "version": 0, "verified": true,
     "pages": [{"index": 0, "width": 595.0, "height": 842.0,
                "original": "/anonymizations/<id>/pages/original/0",
                "anonymized": "/anonymizations/<id>/pages/anonymized/0?v=0"}],
     "boxes": [{"id": 0, "page": 0, "rect": [40.0, 92.1, 120.5, 103.0],
                "label": "PERSON", "layer": "native", "redact": true}],
     "labels": {"PERSON": "Nomi", "FISCAL_CODE": "Codice fiscale"}
   }
   ```

   - Original on the left, anonymized on the right: the two image URLs, relative
     to the service root; `dpi` (50..200, default 110) may be added.
   - `width`/`height` and `rect` are PDF points, top-left origin, as the page is
     displayed: scale by `image_width_px / width` to draw the boxes on the image.
   - Boxes carry no text: the value is visible on the original page.
   - `version` changes with every edit, and so do the anonymized URLs (no stale cache).
3. The reviewer draws a rectangle on the original page to redact it, or clicks a
   box to remove or restore it: `POST /{id}/edits` with `add` (rectangles in the
   same space as `rect`), `remove` / `restore` (box ids). The answer is the new
   result; `GET /{id}/preview` again gives the new boxes and URLs.
4. `GET /{id}/download/pdf` (or `docx`) once `verified` is true.

### Fast mode (`anonymization_core/export_markdown.py`)

The text is the one of the Word export (native layer, or OCR on scanned pages),
line by line, with:

- `<!-- pagina N -->` before every page;
- every redacted value replaced by its label tag: `[NOME]`, `[CODICE FISCALE]`,
  `[INDIRIZZO]`, ... (`taxonomy.py`, column `tag`);
- every table found by LiteParse (`extract_blocks`, ruled or borderless) as a
  Markdown table, first row as header, each cell anonymized like any other text
  (`| [NOME] | [CODICE FISCALE] | negativo |`). When some text inside the table
  falls in no cell, the table is exported as lines instead: no text is lost;
- `[IMMAGINE]` in place of every figure, in reading order: pictures are not
  read in fast mode.

Before release, no redacted identifier or full name may appear in the file
(`verify_markdown`, the same check as the Word export); otherwise the download
is refused. Scanned pages have no LiteParse blocks: their OCR text, tables
included, is exported line by line.

Errors are `ServiceError` subclasses with generic Italian messages (`errors.py`):
`UNSUPPORTED_FORMAT` 415, `FILE_TOO_LARGE` 413, `TOO_MANY_PAGES` 413,
`UNREADABLE_DOCUMENT` 422, `PASSWORD_PROTECTED` 422, `TOO_MANY_JOBS` 429,
`JOB_NOT_FOUND` 404, `JOB_NOT_READY` 409, `MODE_NOT_SUPPORTED` 409,
`VERIFICATION_FAILED` 409.

## Jobs and privacy (`services/jobs.py`)

- One job at a time (single worker); edits go
  through the same worker.
- Documents live in a private folder (`0700`) only while the job exists; they
  are deleted after `ANONPIP_JOB_TTL_MINUTES`, on `DELETE`, on cancel and on shutdown.
- Jobs are in memory: with more than one replica, the ingress needs session
  affinity, or the deployment runs one replica (see `DEPLOY_HANDOVER.md`).
- URLs carry only random 32-hex job ids. Downloads have a generic name
  (`documento_anonimizzato.pdf`): the original file name may contain the patient's name.
- Logs never contain text, values or file names. Processing errors log only the
  exception type.

### KPI events (contract with KPI-dashboard)

| Event | Keys |
|---|---|
| `anonymization_job_created` | `job_id`, `pages`, `size_kb`, `source_format`, `mode` |
| `anonymization_completed` | `job_id`, `pages`, `scanned_pages`, `boxes`, `values_by_label`, `verified`, `edits`, `seconds_total`, `timings`, `mode` |
| `anonymization_edited` | `job_id`, `added`, `removed`, `restored`, `search_hits`, `verified`, `seconds` |
| `anonymization_failed` | `job_id`, `error` (exception type) |
| `anonymization_cancelled` | `job_id` |
| `anonymization_job_deleted` | `job_id`, `reason` (`user`, `expired`) |

`apps/api/tests/unit/test_logging.py` pins the keys and checks that no PII reaches the logs.

## Synthetic dataset and evaluation

The generator, the scoring and the viewer are the evaluation package
(`apps/eval`), which lives on `ref/mod-separation`, not in this branch. What
follows describes how the engine was measured and the results.

**Dataset**: seven Italian document types (discharge
letter, lab report, radiology report, ER report, referral letter, sick-leave
certificate, electronic prescription), each in three variants: native PDF,
scan (skew, paper tint, noise, blur, JPEG, some in grayscale) and scan with
an invisible OCR layer. Traps: names in coloured headers, repeated footers and
rotated margin text, invisible white text, metadata, an annotation deleted but
still in the file history, patients whose surname is an eponym (Corti, Pacini,
Morgagni), eponyms, drugs, signatures, stamps, barcodes with the codice fiscale
and NRE, QR codes with personal URLs, PII in table cells.

Ground truth is exact and fair: each document is rendered twice from the same
seed. In the first copy every PII mention is drawn in a colour that encodes its
id (offset of at most 15/255 from black or white), which gives the box of every
mention, including inside tables and rotated text; that copy is then deleted.
The dataset PDF is the second, clean copy, with pure colours: a check asserts
that both copies have identical words and positions. Ground truth lives in
`dataset/ground_truth/`, separate from the inputs.

**Evaluation dataset** (`dataset/`): 201 documents (329 pages), each in the
three variants above with ground truth in the same format: 603 PDFs.
186 are Italian and 15 English. The test split (`--split test`) holds the
held-out documents: the split is fixed by template family
(about 30% of the 51 families, 78 documents), so no sibling of a test document
is ever seen while tuning; `--split dev` is the rest (123 documents). One
exception: `e056_orientamento_ocr` falls in the test split by its family but was
used to develop the orientation fixes, so the test split holds one document seen
while tuning. The `doc_id` prefix tells
the sources apart (the generator rebuilds only the `NNN_` documents):

| Prefix | Documents | Content |
|---|---|---|
| `NNN_` | 42 | the generator above, 6 of each of its 7 types |
| none | 96 | eight Italian report types in three difficulty levels (facile, media, complessa): repeated headers and footers, codice fiscale in character boxes, stamps, signatures, barcodes and QR codes, eponyms. Boxes recovered from a value-level ground truth; clinical content (diagnoses, drugs, lab values) is not PII, so redacting it counts as over-redaction |
| `eNNN_` | 57 | edge cases: fax with the codice fiscale in character boxes, phone photo with perspective and handwritten PII, landscape table on a `/Rotate 90` page, PII in AcroForm fields, X-ray with PII burned into the pixels and a DICOM header dump, handwritten note on a scan plus the same scan with an OCR layer, PII in metadata, attachments, sticky notes, white text, text under an image and a 2 pt footer, surnames that are cities or words, foreign patients (STP/ENI, passports, diacritics), US and UK documents, and `e056_orientamento_ocr`: a patient label pasted upside down, codice fiscale and date of birth in character boxes, a page scanned upside down and a page sent by fax |
| `d3_` | 6 | hand-annotated worst cases: mixed scanned and native pages, phone photo, fax, hidden channels, US forms |

The dataset is not regenerable as a whole (only the `NNN_` documents are), so
back up `dataset/` before deleting it. On the documents whose native PDF is
already an image (fax, photo, scans) the `scan_ocr` variant has no OCR layer.

**Evaluation** (metrics from standard libraries):

| Metric | Definition | Library |
|---|---|---|
| Recall, precision, F1 (box) | Every required ground-truth box is a positive, found when redaction boxes cover at least 75% of it (honorific excluded; below that part of the value may stay readable); every redaction box touching no ground truth is a false positive; boxes on quasi-identifiers only (clinical dates, facility data) are ignored | scikit-learn |
| Precision, recall, F1, accuracy (word) | Every word of the page: PII or not, redacted or not; quasi-identifiers skipped. The only level with true negatives, hence the only meaningful accuracy. On scans the words of the native twin are mapped through the transform the generator applied | scikit-learn |
| AP@0.5 (PII) | COCO average precision, IoU 0.5, detections ranked by their confidence score, label-agnostic (PII vs not), quasi-identifiers as `iscrowd` ignore regions, each page an image | torchmetrics (pycocotools) |
| mAP@0.5 | Mean of the per-label AP@0.5 (the predicted label must match) | torchmetrics (pycocotools) |

Plus hidden PII leaked (white text, metadata, deleted notes), eponyms wrongly
redacted, verified documents and seconds per page.

Results on the whole evaluation set (201 documents x 3
variants, 603 PDFs; 21/10/2026), with the AILAB NER service. In brackets the
results before the OCR orientation and character-box changes (200 documents,
20/10/2026, same NER service; see "OCR choice"):

| | native | scan | scan + OCR layer |
|---|---|---|---|
| Recall (box, required PII) | 0.940 (0.938) | 0.931 (0.909) | 0.942 (0.939) |
| Precision (box) | 0.960 (0.959) | 0.940 (0.928) | 0.893 (0.884) |
| F1 (box) | 0.950 (0.948) | 0.935 (0.918) | 0.917 (0.911) |
| Precision / recall / F1 (word) | 0.947 / 0.950 / 0.948 | 0.791 / 0.947 / 0.862 | 0.743 / 0.956 / 0.836 |
| Accuracy (word) | 0.987 (0.987) | 0.961 (0.950) | 0.952 (0.943) |
| AP@0.5 (PII, label-agnostic) | 0.657 (0.658) | 0.586 (0.576) | 0.504 (0.502) |
| mAP@0.5 (mean over PII types) | 0.672 (0.670) | 0.587 (0.564) | 0.566 (0.559) |
| Hidden PII leaked (white text, metadata, OCR layers) | 0/324 (0/324) | n/a | 1/324 (1/324) |
| Eponyms wrongly redacted | 53/416 (54/415) | 75/416 (72/415) | 102/416 (100/415) |
| Documents passing verification | 201/201 | 201/201 | 201/201 |
| Seconds per page (laptop RTX 5090, 24 GB) | 0.6 | 3.0 | 3.9 |

The OCR changes act on scans: on plain scans recall goes from 0.909 to 0.931
(codice fiscale 93% to 98%, tessera sanitaria 91% to 100%, persons 94% to 96%)
and precision rises too, because lines that were dropped or read as fragments
are now read whole. On scans with a hidden OCR layer the gain is smaller: that
layer already gave the text. Native pages are not read by OCR; their small
changes are run-to-run variation of the NER service. Seconds per page depend on
the load of the machine and of the NER service during the run, so they are not
comparable between runs.

The hidden value left is a foreign surname in a hidden channel of a scan
("Okafor"), which the NER service does not always find (see "Known limits").
The evaluation of `ref/mod-separation` refreshes the table; `--split test`
scores only the 78 held-out documents.

Reading the numbers:

- Recall is the safety metric (a missed value is a leak); precision measures
  over-redaction, mostly facility addresses and phones in the headers.
- AP is lower than recall because it also scores localisation (IoU 0.5) and
  ranking, which redaction does not need. On scans with a hidden OCR layer the
  boxes of that layer are misaligned with the visible text: they are real
  redactions, but AP counts them as duplicate detections (NMS cannot merge
  boxes that overlap less than 0.5). On scans signatures are covered by an ink
  box smaller than the image box of the ground truth. Address, CAP and city are
  annotated separately but often detected as one box.
- Per-type recall, every miss and AP per type are listed in the report the
  evaluation writes (`reports/evaluation.md`).

## OCR choice

Measured in October 2026, before the move to LiteParse, on the 120 scanned
documents of the first dev split, with the pipeline of the time (a box is found when redaction covers 75% of it), only the
OCR changing, on a laptop RTX 5070 (8 GB) with nothing else running:

| OCR | Recall | Missed PII boxes | Precision | F1 | AP@0.5 / mAP@0.5 | s/page |
|---|---|---|---|---|---|---|
| docTR (db_resnet50 + parseq), the former default | 0.875 | 535 | 0.947 | 0.909 | 0.597 / 0.512 | 1.8 |
| **PaddleOCR PP-OCRv5, server detector (RapidOCR)** | **0.900** | **426** | 0.947 | **0.923** | 0.584 / **0.571** | 4.0 |
| PaddleOCR PP-OCRv6 medium (official package), first 90 documents | 0.750 | | 0.897 | | | 2.4 |

PaddleOCR PP-OCRv5 with the server detector was chosen: one missed value in
five fewer than docTR at the same precision. Its label-agnostic AP is slightly
lower because AP also scores box tightness and ranking, which redaction does
not need. It is Apache 2.0. PP-OCRv5 has a larger "server" model only for
detection; the Latin recogniser is the mobile one either way. The same models
now run in the OCR server of `services/ocr-server/` (RapidOCR on ONNX Runtime), where
`OCRSRV_DET_MODEL=PP-OCRv5_mobile_det` trades some accuracy for speed.

PP-OCRv6 scores higher than v5 in PaddleOCR's own benchmarks, but those are
in-house sets, largely Chinese, compared with the Chinese v5 recogniser. Its
recogniser has one dictionary for 50 languages (18,708 characters, 15,565 of
them Chinese, against 836 Latin characters for the v5 Latin model) and on our
Italian documents it drops the spaces after punctuation ("Salerno,15/03/2026",
"Cod.regionale724932") and adds accents ("Gaglíardǐ"): values glued to their
neighbours are no longer recognised. The official package also returns line
boxes only, so word boxes have to be estimated.

### Orientation and character boxes

Two changes to how PP-OCRv5 is run, measured step by step on the scans of the
evaluation set (box level; the first three rows on the 200 documents of
20/10/2026, the last on the 201 documents of 21/10/2026), everything else
identical. They were measured with the former in-pod OCR (PyMuPDF rendering,
`ocr.py`) and then moved as they are to the OCR server (orientation) and to
`cells.py` with the server's `POST /recognize` (character boxes); the current
stack has not been measured with them yet:

| | scan recall | scan precision | scan F1 | scan + OCR layer F1 |
|---|---|---|---|---|
| Before (line classifier on, boxes read by the page OCR) | 0.908 | 0.923 | 0.916 | 0.902 |
| Line orientation classifier off, upside-down pages read turned | 0.920 | 0.938 | 0.929 | 0.916 |
| Plus character boxes read again cleaned | 0.930 | 0.939 | 0.935 | 0.916 |
| Plus upside-down lines read turned, guards on turned pages and valid codes | 0.931 | 0.940 | 0.935 | 0.917 |

- **The line classifier.** RapidOCR passes every detected line to an
  orientation classifier; on our scans it turned upright, perfectly legible
  lines by 180 degrees ("Paz. Zanetti Carlo Alberto - nato il 30.08.1953 - Acc.
  TC2025-118842", "la cugina Khady Sow (+39 389 0047215)"). The recogniser then
  read noise (confidence about 0.35) and RapidOCR dropped the whole line under
  its 0.5 threshold. Without the classifier the same lines are read at 0.98 to
  0.99. Of the values missed on plain scans but found on the native twin, about
  a fifth were lines never read at all; most of them are back.
- **Character boxes.** About 80 of those values were codes in boxes broken by
  specks. Read again as one clean word, the ten rows of the misses were all read
  right; on the evaluation set codice fiscale goes from 94% to 98% and tessera
  sanitaria from 91% to 100% on plain scans, and no document loses a code. A
  first version replaced the page OCR also when only part of a row was found
  (a lost border) and lost two codes the page OCR had read whole: the guards
  described in "Text layers" come from that.
- **Edge cases.** `e056_orientamento_ocr` holds what the evaluation set did not
  have: a label pasted upside down, a page scanned upside down, a fax. Before
  the changes its plain scan scored recall 0.80 (1 value of 7 on the upside-down
  page, the label's record number and phone missed); now 1.0 on every variant,
  and the fax page is never swapped for a turned reading.
- **Cost.** Finding the box rows takes about 0.08 s per page; each row and each
  low line read again is one recognition call on a small crop (CPU). A page is
  read twice only when its mean confidence is below 0.8, which no page of the
  evaluation set reaches (its lowest is 0.98).
- **Side effects.** More lines read means more text to misjudge: eponyms wrongly
  redacted on scans went from 70 to 75 of 415. On 60 upright pages the turned
  re-reading of low lines added one piece of noise (a signature read as two
  short words) and removed no word read with confidence.

### Speed

- **Detector input size**: RapidOCR shrinks every image to a long side of 2000
  px before detection (its `Global.max_side_len`), so a 300 dpi A4 page reaches
  the detector at 1984 x 1408.
- Measured on 12 documents per variant before the switch to the AILAB NER
  service (RTX 5070 8 GB, OCR server and in-pod GLiNER NER on the same GPU):
  0.7 s per native page, 2.4 s per scanned page, 2.5 s per scan with an OCR
  layer, verification included.
- Native pages are not read by OCR, except the pictures on them (X-rays), whose
  burned-in text is read.
- **GPU memory**: only the OCR server uses a GPU; it peaks at about 4 GB per
  page and releases it after every run (0.3 GB idle); an optional cap is
  `OCRSRV_GPU_MEM_LIMIT_MB` (0 = none). NER runs in the AILAB NER service.

Not adopted:

- **GLM-OCR** (via Ollama) reads stamps and faded fax lines better than docTR
  (0.7 s per crop), but it returns text without boxes: aligned to docTR's word
  boxes on the uncertain lines it changed nothing measurable, for +50% time.
- **LightOnOCR-3** (Apache 2.0, 0.8B): block-level boxes only; text-only use
  would sit in the same place as GLM-OCR.
- **Chandra OCR 2** (5B): block-level layout, and its weights need a commercial
  licence for self-hosting.
- **A contextual LLM detector** (qwen3-vl 8B via Ollama) raised F1 on native
  PDFs from 0.949 to 0.965 on a sample, at about 30 times the time per page.
- **Docling**: document structure (tables, reading order), with OCR delegated
  to an engine like the one above and no word boxes.

## NER choice

The measurements of this section were taken before the OCR changes of
21/10/2026 (see "OCR choice"); the comparison holds, both NERs read the same text.

NER is delegated to the AILAB NER service already used by the platform
(`ANONPIP_NER_URL`): the pipeline sends the text of each page and keeps only
the entities, never its `obfuscated_text`. The configuration endpoints of the
service (`/medical_orgs`, `/medical_labels`) are shared with ai-platform and
never called.

Measured against the earlier in-pod GLiNER + GLiNER2 (commit `92ae6e5`) on the
same 600 PDFs, everything else identical; differences in points, AILAB minus
GLiNER, 95% interval from a document bootstrap:

| | native | scan | scan + OCR layer |
|---|---|---|---|
| Recall, dev / test | -1.0 / -1.0 | -0.6 / -0.6 | -0.6 / -0.5 |
| Precision, dev / test | -0.5 / -2.5 | -0.9 / -1.4 | -2.2 / -1.8 |
| Values found only by GLiNER / only by AILAB (all) | 86 / 18 | 77 / 36 | 64 / 29 |

The values lost are almost all persons and places (the other labels come from
the rules; one phone per variant moves through propagation). Their causes, all
in the service output:

- eponyms of its dictionary are replaced before the model, also when they are
  surnames or first names ("Alessia Corti" -> "Alessia EPONYM3", "Vittoria"
  -> "EPONYM2"): the replaced word does not exist in the original text and is
  dropped, so the name is redacted only in part;
- words split into pieces with the middle piece missing ("ME" + "##INA" for
  "MESSINA"), which cannot be aligned;
- foreign names and dense lists of patients (shift handovers) with no entity
  at all ("Emeka Okafor", "Popescu Ioana");
- places with the country in brackets found in part ("Casablanca (Marocco)").

The extra over-redaction comes from fragments taken for places ("il", "C",
"PUGLIA"), medical eponyms taken for persons ("Marsh-Oberhuber", "Ishak") and
label words joined to names ("CAIRONI DANILO Nato"). Joining word pieces and
cutting spans at the next field (see `ner.py` above) recovered +0.1 to +0.3
points of recall and +0.3 to +2.0 points of precision; the rest needs either a
change in the service (offsets on the original text, or no `EPONYM`
replacement on the entities) or further filters on our side.

Earlier benchmark (before the switch to the service): six on-premise PII models (open weights, run in the pod, no external call) in
place of the default, on the 108 native documents of the dev split (179-document set), every other
part of the pipeline identical (75% coverage, laptop RTX 5070):

| NER model | Recall | Precision | F1 | AP@0.5 / mAP@0.5 | s/page |
|---|---|---|---|---|---|
| `urchade/gliner_multi_pii-v1` | 0.937 | **0.973** | **0.955** | 0.648 / 0.653 | 0.65 |
| `fastino/gliner2-privacy-filter-PII-multi` (GLiNER2, 2026) | **0.942** | 0.962 | 0.952 | **0.664 / 0.671** | 0.62 |
| `DeepMount00/GLiNER_PII_ITA` (Italian only) | 0.932 | 0.970 | 0.950 | 0.659 / 0.669 | 0.65 |
| `knowledgator/gliner-pii-large-v1.0` | 0.925 | 0.967 | 0.946 | 0.647 / 0.665 | 0.73 |
| `nvidia/gliner-PII` | 0.920 | 0.959 | 0.939 | 0.628 / 0.668 | 0.73 |
| `openai/privacy-filter` (mainly English) | 0.925 | 0.940 | 0.933 | 0.616 / 0.638 | 1.36 |

| **GLiNER + GLiNER2 together** (default at the time) | **0.948** | 0.961 | **0.954** | 0.654 / 0.653 | 0.7 |

No single model beats GLiNER on F1; the two best together find 1.1 points more
recall than GLiNER alone (17% fewer missed values) at the same F1: the extra
boxes are over-redaction, the safe error. Both are multilingual: on the English
documents of the dev split they tie the best (recall 0.964), while the
Italian-only model drops there (0.945). GLiNER alone had precision 0.973 and
recall 0.937. Recall depends far more on the
rules, the labels and propagation than on the NER: the models differ by 2
points at most. GLiNER2 (`gliner2`, Apache 2.0, Fastino) is loaded with
`from_pretrained`, like GLiNER; its API client for Fastino's cloud is never used.

## Known limits

- Handwritten PII on scans is not detected (0 of 12 handwritten boxes in the
  scanned dev documents): the UI edits cover it. DOCX/TXT input is not supported.
- Reversible mode (encrypted mapping, reconstruction of the original) is out of scope.
- Professional IDs and some phones on plain scans depend on OCR quality; the
  UI edits cover the misses.
- Pages with a box become images: the text stays searchable through an
  invisible layer, but it is no longer vector text, and native documents grow
  by about 45%.
- Character boxes are found only when their ruling is dark and continuous:
  light grey or red dropout grids, dashed boxes and the two fax prescriptions of
  the evaluation set are left to the page OCR (24 codici fiscali still missed on
  plain scans, measured with the former in-pod OCR). Boxes tuned on synthetic
  forms with 14 to 15 pt cells.
- Upside-down pages and lines are measured only on `e056_orientamento_ocr`;
  a page whose upside-down reading looks confident (mean above 0.8) is not read
  again turned. Text turned by angles other than 90 and 180 degrees is not
  handled.
- NER depends on the AILAB NER service: names it replaces as eponyms ("Corti",
  "Vittoria"), foreign names and dense lists of patients can be missed or
  redacted in part (see "NER choice"); the text of each page leaves the pod for
  the service; if the service is down, jobs fail (fail-closed).
