# Test fixtures

Configuration of the tests only, never read by the service:

| File | Purpose |
|---|---|
| `pipeline.yaml` | pipeline overrides (`make_settings`): every category active, whatever the packaged defaults |
| `categories.yaml` | category registry read by `tests/conftest.py` instead of `resources/categories.yaml`: same groups and categories, generic families and partial categories always active. `test_pipeline_config.py` checks that its groups stay aligned with the packaged file. |

Documents added here are **synthetic** (fictitious data). Never add real documents.
