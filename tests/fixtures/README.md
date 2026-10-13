# Test fixtures

**Synthetic** documents (fictitious data) from the corpus of the anonimizzatore project, used by
the tests and for manual runs:

| File | Kind |
|---|---|
| `referto_pdf_nativo.pdf` | cardiology report, PDF with a native text layer |
| `referto_scansione.pdf` | the same report, medium-quality scan |
| `documento_identita_foto.jpg` | photographed health card (front and back) |
| `ricetta.txt` | electronic prescription reminder, plain text |

Never add real documents here.

Configuration of the tests only, never read by the service:

| File | Purpose |
|---|---|
| `models.yaml` | models catalog of the tests (`make_settings`) |
| `pipeline.yaml` | pipeline overrides (`make_settings`): every category active, whatever the packaged defaults |
| `categories.yaml` | category registry read by `tests/conftest.py` instead of `resources/categories.yaml`: same groups and categories, generic families and partial categories always active. Keep its groups aligned with the packaged file. |
