# OCR server contract

The engine reads scanned pages (and the grayscale pictures of native pages)
through an OCR server over HTTP: `LiteparseReader` in `reader.py` is the
client. Any server that implements the three endpoints below works. The
reference implementation is `ocr-server` in this repository.

`/ocr` follows the LiteParse OCR API (`OCR_API_SPEC.md` of run-llama/liteparse),
with one result per word. `/health` and `/recognize` are additions of this
project.

Both sides pin the contract with tests:

- engine: `tests/test_ocr_contract.py` checks the requests the reader sends
  and how it reads the answers, against `anonymization_core.testing.OcrServerStub`;
- server: `ocr-server/tests/test_contract.py` checks that the answers
  have this shape.

Change this file and both tests together.

## `GET /health`

| | |
|---|---|
| 200 | Models loaded: the reader becomes ready (`load()`). The body is not read (the reference server sends `{"status": "healthy"}`). |
| any other status | Not ready: `load()` raises, the pipeline stays not ready and the service answers `/readyz` with 503 (fail-closed). |

## `POST /ocr`

Every word of one page image.

Request, `multipart/form-data`:

| Part | Value |
|---|---|
| `file` | PNG of the page as displayed (rotation applied), rendered at the reader's resolution |
| `language` (form field) | `it` |
| `X-Render-DPI` (header) | Resolution of the image, `PipelineConfig.ocr_dpi` (default 300) |

Response 200, JSON:

```json
{
  "results": [
    {
      "text": "RSSMRA85M01H501U",
      "bbox": [412.0, 980.5, 731.0, 1012.0],
      "confidence": 0.9731,
      "polygon": [[412.0, 980.5], [731.0, 980.5], [731.0, 1012.0], [412.0, 1012.0]]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `text` | One word. A vertical line may come as one result holding its words; the reader splits it along the reading direction. Empty results are skipped. |
| `bbox` | `[x0, y0, x1, y1]` in pixels of the image sent, top-left origin. The reader scales by `72 / dpi` to PDF points. A result without four values is skipped. |
| `confidence` | 0 to 1. Missing: 0. |
| `polygon` | Optional: four `[x, y]` corners, first corner the top-left of the text as read, then the top-right. The reader takes the reading direction from the longer side (left to right, right to left, top to bottom, bottom to top); without it, left to right. |

Any status other than 2xx raises in the reader: the job fails and nothing is
released.

## `POST /recognize`

Recognition only (no detection), on an image that holds one word or line.
The reader sends the characters of a row of boxes (a codice fiscale written in
cells) to read them again.

Request, `multipart/form-data`: `file`, a PNG.

Response 200, JSON: `{"text": "RSSMRA85M01H501U", "confidence": 0.9123}`.
Missing fields read as `""` and `0.0`. Any status other than 2xx raises.

## Errors

The reference server answers `{"error": "<generic message>"}` with 400
(unreadable or too large image), 503 (models not loaded) or 500 (OCR
failure). The reader only looks at the status.

## Privacy

Page images are health data. The server must not log the text it reads and
must not write images to disk. The engine sends nothing else: no file name,
no document id.
