# CLAUDE.md - AGENAS MIA service

This repository was generated from `agenas-python-template`. It is a Python
service of the **MIA (Medical Information Access)** platform, a RAG-based
assistant for Italian general practitioners (medici di medicina generale),
built for AGENAS. The platform handles **health data of Italian citizens**:
privacy rules below are not optional.

Read this file entirely before the first change. Detailed references:

- `docs/LOGGING_AND_PRIVACY.md` - logging levels, what may and may not be logged, the Loki/KPI contract
- `docs/DEPLOY_HANDOVER.md` - what the developer hands over, what the platform team provisions
- `docs/AI_PLATFORM_INTEGRATION.md` - read it if the feature lives **inside ai-platform** instead of this service

## 1. Where this service sits

```
GP (doctor, SPID/CIE)
    |
    v
[WSO2 API Manager]  validates the JWT, forwards X-JWT-Assertion
    |
    +--> [authz]          doctor authorization (codice fiscale -> regione/asl/ruoli/prodotti)
    +--> [ai-platform]    RAG: query -> embed -> retrieve (Weaviate) -> rerank -> LLM answer
    |        +--> [llm-gateway]     the ONLY way to reach an LLM provider (EU residency)
    |        +--> [ner-anonymizer]  blocks PII before it reaches the LLM (fail-closed)
    +--> [this service]

[fe]                       Next.js frontend for doctors
[validation-flow-gitlab]   knowledge-base upload + committee approval
[KPI-dashboard]            reads service logs from Loki -> log format is a contract
[oke-staging / oke-prod]   Flux CD repos, reconciled into Kubernetes (OKE)
```

- Identity arrives already validated by WSO2 (and authz). Do not implement
  your own JWT validation or user database unless the platform team agrees.
- If this service needs an LLM, call llm-gateway. It exposes an
  OpenAI-compatible API, so the `openai` client is fine as long as its
  `base_url` points to the gateway. Never call OpenAI, Gemini, Bedrock and
  so on directly. The per-application gateway key is requested from
  Pasqualino, with Maurizio Di Pierro in cc (see README, "Mi servono gli LLM").
- If user free text could reach an LLM, it must first go through
  ner-anonymizer, and the call must **fail closed**: if NER is down, the
  request is refused (HTTP 503), never forwarded.

## 2. Mandatory stack

| Concern | Choice |
|---|---|
| Language | Python 3.12, type hints everywhere (`list[str]`, `X \| None`) |
| Packages | `uv` only. Never `pip install`, never `python ...` without `uv run` |
| Web | FastAPI + uvicorn, app factory `create_app()` in `main.py`, routers in `api/` |
| Config | pydantic-settings, `config.py`, env prefix `TEMPLATE_` |
| Database | PostgreSQL 17, SQLAlchemy 2 async + asyncpg, schema via Alembic only |
| HTTP client | `httpx.AsyncClient`, created in `lifespan`, stored on `app.state` |
| Lint / format | ruff (`print()` is banned by rule T20) |
| Types | ty |
| Tests | pytest, pytest-asyncio (`asyncio_mode = "auto"`), pytest-cov |
| Task runner | `just` |

Layout: `api/` (thin routers) -> `services/` (business logic) -> `db/`
(models, engine). Shared clients live on `app.state`, never as module
globals. All imports at module level (no lazy imports). Google-style
docstrings on public functions. No redundant comments. No emojis anywhere.

## 3. Commands

```
just up / down / logs        compose stack (service + Postgres 17)
just dev                     API on the host, DB in compose
just lint                    ruff fix + format + ty    <- before every commit
just check                   same checks, no fixes (what CI runs)
just test                    unit tests
just test-int                integration tests on the compose Postgres
just test-cov                unit tests + coverage (fails under 80%)
just revision "msg"          autogenerate an Alembic migration
just migrate / downgrade     apply / roll back migrations
just audit                   dependency vulnerabilities
```

**Definition of done for every task:** `just lint` and `just test-cov` pass,
and `just test-int` passes if the change touches the database.

## 4. Local development: always compose + Postgres

- Develop against the Postgres in `docker-compose.yml` (`postgres:17`, same
  major as the CloudNativePG clusters in staging/prod). No locally installed
  database, no SQLite, no mocks for the database in integration tests.
- The compose DB has two databases: `app` (development) and `app_test`
  (integration tests, created by `scripts/db-init/`).
- **We do not provision databases ourselves.** In staging/prod the platform
  team creates a CloudNativePG cluster and the service receives one variable,
  `TEMPLATE_DATABASE_URL`, from the CNPG secret (`<cluster>-app`, key `uri`).
  `config.py` already converts that `postgresql://` URI to the asyncpg driver.
- Schema changes go **only** through Alembic migrations in `migrations/versions/`.
  Never `Base.metadata.create_all()`, never DDL in application code.
  Every migration has a working `downgrade()`; `just test-int` runs both.
- Migrations run at container start (`scripts/entrypoint.sh`) under a
  Postgres advisory lock, so two replicas never migrate at once. Write them
  to be backward compatible with the previous image (expand, then contract):
  during a rolling update old and new pods share the same schema.

## 5. Tests

- **TDD**: Red -> Green -> Refactor. Write the failing test first.
- `tests/unit/`: no database, no network. Override dependencies
  (`app.dependency_overrides`) with in-memory fakes, see `tests/unit/conftest.py`.
- `tests/integration/`: marked `integration`, real Postgres, run with `just test-int`.
- Coverage: at least 80% (`fail_under`); SonarQube enforces coverage on new code.
- Build settings in tests with `make_settings(...)` from `tests/conftest.py`,
  never from the developer's environment.
- **Every feature that handles user text or personal identifiers has a
  non-leak test**: use the `log_stream` fixture (the real production handler)
  and assert that the raw value is absent from the logs. Example:
  `tests/unit/test_logging.py::test_item_creation_never_logs_user_text`.
- Test data is synthetic. The only codice fiscale in fixtures is the textbook
  `RSSMRA85M01H501U`. Never paste real data from staging or prod.

## 6. Logging

Details and examples in `docs/LOGGING_AND_PRIVACY.md`. Rules:

- stdlib `logging`, one logger per module: `logger = logging.getLogger(__name__)`.
  Configured once by `setup_logging()` (`logging.basicConfig` is banned by ruff).
- Plain-text lines on stdout, format
  `%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s`.
  Grafana Alloy ships stdout to Loki. Never write log files.
- Use lazy `%s` formatting: `logger.info("Loaded %d documents", n)`, not f-strings.
- Levels:
  - `DEBUG` - diagnostic detail. Local and staging only (forced to INFO when `ENVIRONMENT=prod`).
  - `INFO` - lifecycle and business events, without content.
  - `WARNING` - degraded but handled (retry, fallback, dependency down).
  - `ERROR` - request failed; use `exc_info=` (traceback goes through redaction).
- **Never log, in any environment:** codici fiscali in clear, names, emails,
  JWTs, API keys, raw headers, raw request/response bodies, DB connection
  strings, the `input` of a validation error.
- **Debug content, never in prod:** user-entered text (questions, chat
  messages, notes, free-text fields), LLM prompts and LLM responses. They may
  be logged in local and staging for debugging, only:
  - through `redact_user_text(text, settings)`, never directly;
  - at `DEBUG` level;
  - when `TEMPLATE_EXTENDED_LOG_CONTEXT=true` is set. It is off by default,
    so switch it on only for the time the investigation needs.
  In prod `redact_user_text` always returns `<redacted>` (the flag is forced
  off and DEBUG is forced to INFO). In every environment it still masks
  codici fiscali inside the text.
- Personal identifiers: log `hash_identifier(cf)` (SHA256, same normalisation
  as authz, so values can be joined) or `mask_identifier(cf)` (`R***U`).
- `PiiRedactingFilter` masks codici fiscali in everything that reaches stdout
  (including tracebacks and uvicorn). It is a safety net, not a licence to
  log raw data.
- Events meant for dashboards: `log_event(logger, "snake_case_name", {...})`
  -> `snake_case_name {json}`. The payload holds only counters, ids, hashes,
  enums. Event names and keys are a contract with KPI-dashboard: never rename
  them, and cover each event with a test.

## 7. Privacy and GDPR

- Data minimisation: store only what the feature needs; prefer hashed
  identifiers (`hash_identifier`) over plaintext codici fiscali.
- No personal data in URLs or query strings (they end up in access logs).
- User-facing errors are generic Italian messages (`errors.py`): never a
  stack trace, SQL error or echo of the input. The 422 handler already strips
  `input`.
- LLM calls only through llm-gateway (EU data residency). PII gate on
  ner-anonymizer, fail-closed.
- `X-Request-ID` is accepted from callers only if it looks like an id;
  otherwise a new one is generated.

## 8. Configuration and environment variables

- Every setting lives in `Settings` (`config.py`) with a **safe default**:
  a missing variable must never crash a pod. `os.getenv` is banned by ruff.
- Adding a variable means updating three files together: `config.py`,
  `.env.example`, `docker-compose.yml` (`${VAR:-default}` syntax).
  Then list it in `docs/DEPLOY_HANDOVER.md`.
- **Credentials never have a default**, not even a local or example one:
  database URLs, passwords, API keys, tokens. In `Settings` they default to
  `""`, and the service handles the empty value without crashing (see
  `database_url`: the API starts and `/readyz` returns 503). In
  `docker-compose.yml` they use `${VAR:?message}`, never `${VAR:-value}`.
  Local values live only in `.env`, copied from `.env.example`.
- In the cluster, secrets come from Vault via External Secrets, or from the
  CNPG secret for the database. The platform team wires them. They never
  appear in logs.
- `TEMPLATE_ENVIRONMENT` defaults to `prod`, the most conservative
  behaviour: redaction on, DEBUG off, OpenAPI docs off.

## 9. API conventions

- Mandatory endpoints (Kubernetes probes):
  - `GET /healthz` - liveness, no dependency checks.
  - `GET /readyz` - readiness, checks the database (503 if unreachable).
  - `GET /version` - name and version from the package metadata.
- Request and response bodies are Pydantic models with explicit limits
  (`max_length`, `ge`/`le`). Lists are paginated or capped.
- Errors: raise a `ServiceError` subclass from `errors.py`; do not build
  `HTTPException`s with ad-hoc messages.
- User-facing copy is in Italian; dates and numbers use the `it-IT` locale.
- One uvicorn worker per pod; scale with Kubernetes replicas.

## 10. Git, branches, commits

- Every change starts on a dedicated feature branch, usually created from
  `main`. Never commit directly to `test` or `main`.
- Flow: feature branch -> MR into `test` (CI builds `YYYY.MM.DD-test.<epoch>`,
  Flux deploys to **staging**) -> validate on staging -> MR `test` into `main`
  (CI builds `YYYY.MM.DD`, Flux deploys to **production**).
- The only allowed merges are feature branch -> `test` and `test` -> `main`.
  A feature branch is never merged directly into `main`.
- Small commits, one logical change each, conventional prefixes
  (`feat:`, `fix:`, `chore:`, `test:`, `refactor:`, `ci:`).
- **Never** add `Co-Authored-By:` or any AI attribution to commits.
- **Never commit `CLAUDE.md`** in a service repo (it is in `.gitignore`).
- No emojis in code, comments, docs or commit messages.

## 11. Deploy

- Developers do not edit `oke-staging` / `oke-prod`. Fill in
  `docs/DEPLOY_HANDOVER.md` (image name, env vars with defaults, secrets,
  resources, whether a database is needed) and hand it to the platform team,
  who create the namespace, the CNPG cluster, the ExternalSecrets, the Flux
  Kustomization and the image policies. `docs/k8s-example/service.yaml`
  shows the resulting manifest.
- Never rename the image, the port (8000), the probe paths or the env
  prefix once deployed: the manifests reference them.

## 12. Working inside ai-platform instead

If the feature is a module of ai-platform (a new retrieval strategy, a
pre/post-processor, a new endpoint of the RAG API), this service layout does
not apply. Copy `docs/ai-platform/CLAUDE.md` into the root of your
ai-platform clone (its `.gitignore` excludes `*.md`, so it stays local) and
read `docs/AI_PLATFORM_INTEGRATION.md`. The logging, privacy, testing and git
rules of this file still apply.
