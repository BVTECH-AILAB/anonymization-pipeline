# CLAUDE.md - working inside ai-platform

<!--
How to use this file:
  cp agenas-python-template/docs/ai-platform/CLAUDE.md <your ai-platform clone>/CLAUDE.md
ai-platform's .gitignore excludes *.md, so the file stays local and is never
committed (as required for every CLAUDE.md in AGENAS repos).
Paths below are relative to the ai-platform repo root unless they start
with agenas-python-template/ or another component name.
-->

You are working on **ai-platform** (image `rag-system`, namespace
`ai-platform`), the RAG engine of the MIA platform for Italian general
practitioners, built for AGENAS. It answers clinical questions using Italian
knowledge bases (linee guida, farmaci / AIFA, campaign material) with
citations. It handles **health data of Italian citizens**: the privacy rules
below are not optional.

This is an integration into an existing codebase, not a new service: follow
ai-platform's layout and idioms, not the layout of agenas-python-template.

## 1. Context

```
GP -> fe (Next.js) -> WSO2 API Manager -> ai-platform (/chat, /chat/stream, ...)
                                              |
                                              +-> ner-anonymizer  PII gate, fail-closed
                                              +-> Weaviate        vector search
                                              +-> llm-gateway     every LLM call
                                              +-> Postgres        conversations, feedback, trace logs
ai-platform stdout -> Alloy -> Loki -> KPI-dashboard (log lines are a contract)
```

- The frontend calls authz, then forwards `X-Regione`, `X-ASL`, `X-Ruoli`
  and `X-API-Key` to ai-platform. ai-platform does not validate JWTs itself.
- Query flow (`src/interfaces/mod.py`, `ask_stream()`): preprocess (PII) ->
  retrieve (query rewriting, embedding, vector search, rerank) -> prompt
  construction -> LLM streaming -> citation parsing -> post-processing
  (citations, hallucination, plausibility).

## 2. Branches and release

1. Create a feature branch, usually from `main`:
   `git checkout main && git pull && git checkout -b feature/<short-name>`.
2. MR feature branch -> `test`. CI builds `rag-system:YYYY.MM.DD-test.<epoch>`
   and Flux deploys it to **staging**.
3. Validate on staging, including the logs in Grafana.
4. MR `test` -> `main`. CI builds `rag-system:YYYY.MM.DD` and Flux deploys it
   to **production**.

The only allowed merges are feature branch -> `test` and `test` -> `main`.
Never merge a feature branch into `main`. Never commit directly to `test`
or `main`.

## 3. Commands

- `uv` only: `uv add <pkg>`, and never run `python` without `uv run`.
- `just dev` / `just stop` / `just logs` - Docker Compose stack in `infra/`
  (weaviate, redis, postgres, api, cli).
- `just lint` - fix + format + ty. **Must pass before every push.**
- `just test-fast [path]` - tests in Docker reusing the existing image.
  **Use this by default.**
- `just test` - rebuilds the image first. Only after dependency changes:
  the rebuild is slow and memory-hungry.
- `just testbook --tester <Name> ...` - quality suites (see `evals/README.md`).
- `just cli`, `just queryapi` - try the pipeline end to end locally.

Definition of done: `just lint` and `just test-fast` pass. New behaviour has
tests, and a quality-sensitive change has a testbook run.

## 4. Coding standards

- Python 3.12+, type hints on new and touched code (`list[str]`, `X | None`).
- **No lazy imports**: every import at module level (stdlib, third-party,
  local). The only exception is inside `if __name__ == "__main__":`.
- Local imports use the `src.` prefix: `from src.adapters.llm import get_llm`.
- Google-style docstrings on new and modified functions.
- No redundant comments. No emojis anywhere.
- Type checker is `ty` (`just types`), linter and formatter is ruff.

## 5. Where code goes

| What you are adding | Where | Pattern to follow |
|---|---|---|
| Client for an external service | `src/adapters/<area>/` | Protocol + implementations + factory (`get_llm()`, `get_vectordb()`, `get_embedder()`, `get_preprocessor()`); async context manager (`async with`) |
| Query pre-processing step | `src/adapters/preprocessing/` | `Preprocessor` protocol, chained by `CompositePreprocessor` |
| Answer validation | `src/adapters/postprocess/` | `PostProcessor.check(response, context)` returning `AppWarning`s |
| Query-time logic (retrieval, prompting, guardrail, routing) | `src/strategies/<name>.py` | One module per step; see `guardrail.py`, `document_retrieval.py`, `campaign_routing.py` |
| Request/response models, domain errors | `src/domain/` | Pydantic models in `chat.py`; exceptions in `errors.py`, user messages in Italian |
| New HTTP endpoints | `src/interfaces/<name>.py` | Its own `APIRouter`, mounted in `src/interfaces/api.py` with `include_router` behind a feature flag (pattern: `tracelogs_enabled()` in `src/utils/env.py`) |
| Shared resources (clients, pools) | `lifespan` in `src/interfaces/api.py` | `_connect_*` helper storing the client on `app.state` |
| Ingestion | `src/pipeline/` (`steps/`, `planner.py`, `executor.py`) | `PipelineStep` protocol |
| Prompts and model config | `config/prompts/`, `config/production.json` (via `src/config/models.py`) | |

Keep `src/interfaces/api.py` thin: it is already large. Put logic in
strategies and adapters, and wire it in with a few lines.

New behaviour ships **behind a feature flag whose default keeps the current
behaviour**, so merging into `test` never changes staging until the flag is
enabled in its ConfigMap.

## 6. LLM

- Always `get_llm()` from `src/adapters/llm`, used as an async context manager:
  ```python
  async with get_llm() as llm:
      response = await llm.get_response(messages)
  ```
- It goes through **llm-gateway** (`LLM_GATEWAY_BASE_URL`,
  `LLM_GATEWAY_API_KEY`; in cluster
  `http://llm-gateway-app.llm-gateway.svc.cluster.local:8000`). Never import a
  provider SDK to call OpenAI, Gemini, Bedrock and so on directly.
- Models are gateway aliases (`fast-production`, `strong-production`),
  selected through `LLM_GATEWAY_MODEL` / `LLM_GATEWAY_THINKING_MODEL`.
- Gateway API keys are requested from Pasqualino, with Maurizio Di Pierro in
  cc. Keep a staging key only in your local `.env`; never paste or commit it.
- Unit tests never call the real gateway: fake the `LLM` protocol.

## 7. PII gate: never bypass it

User text reaches the LLM only after the PII preprocessor
(`src/adapters/preprocessing/pii_blocker.py`, `self_hosted_pii.py`, factory
in `__init__.py`) has called **ner-anonymizer** (`NER_API_URL`, modes from
`BLOCK_PII` / `ANONYMIZE_PII`). The behaviour is **fail-closed**:

- NER unreachable -> `NERServiceUnavailableError` -> HTTP 503, or SSE error code `NER_UNAVAILABLE`.
- PII detected -> `PIIDetectedError` -> HTTP 400, or an assistant message in the stream.

Any new path that sends user text, or text derived from it (summaries,
rewritten queries), to an LLM must go through the same gate. Never add a
fallback that forwards the message when NER fails. Never disable the gate in
the prod configuration.

## 8. Logging

- Format: `%(asctime)s %(levelname)s %(name)s [%(thread_uuid)s]: %(message)s`.
  The `[thread_uuid]` prefix is set per request by `src/utils/thread_context.py`.
- Use `logger = logging.getLogger(__name__)` and lazy `%s` formatting.
  Do not add `print()` calls.
- **Never log, in any environment:** codici fiscali, names, JWTs, API keys,
  raw headers, raw request bodies.
- **Debug only, never in prod:** user questions and messages, LLM prompts
  and LLM responses. Log them only through `redact_query()`
  (`src/utils/log_user_input.py`), at DEBUG level. They then appear only when
  `EXTENDED_LOG_CONTEXT=true`.
  Caveat: `redact_query()` checks only that flag, not the environment, so
  production safety depends on the prod ConfigMap keeping
  `EXTENDED_LOG_CONTEXT: "false"` (`oke-prod/ai-platform/rag-system.yaml`).
  Never propose enabling it in prod.
- **KPI-dashboard parses these lines from Loki.** Do not change their text,
  keys or format without agreeing with whoever maintains KPI-dashboard:

  | Line | Emitted in | KPI |
  |---|---|---|
  | `[<thread_uuid>]` log prefix | `src/utils/thread_context.py` | joins between events |
  | `conversation: {json}` (`thread_uuid`, `regione`, `asl`, `roles`, `conversation_type`, `pathology_tags`) | `src/interfaces/api.py` | conversations |
  | `inference_latency {json}` (`client_ttft_ms`) | `src/utils/latency.py` | latency |
  | `Hallucination detection: level=...` | `src/adapters/postprocess/hallucination.py` | hallucinations |
  | `Feedback received: up/down`, `Category: ...` | `src/interfaces/api.py` | feedback |
  | uvicorn access log of `POST /chat/stream` and `GET /` | uvicorn | success rate, uptime |

  Full inventory: `KPI-dashboard/documentation/loki-query-inventory.md`.

## 9. Tests

- `tests/` runs under pytest with `asyncio_mode = "auto"`. The markers are
  `slow`, `testbook` and `audio_e2e`.
- `tests/contract/` is the testbook contract tier (`*-C*`). `evals/` is the
  LLM-judged quality tier (`*-Q*`). The catalog binding testbook IDs to code
  is `testbook/mapping.csv`.
- Under pytest `src/utils/env.py` loads `.env.test`, so tests never depend
  on your `.env`.
- Write the failing test first (TDD). Fake LLM, embedder, vector DB and NER
  through their protocols, with no network in unit tests.
- Any change touching user text needs a **non-leak test**: capture the
  logs (`src/utils/log_capture.py` or `caplog`) and assert that the raw text
  and any codice fiscale are absent.
- Test data is synthetic: never paste real questions or documents from
  staging or prod.

## 10. Configuration and data

- Environment variables are read through `src/utils/env.py`; model and
  ingestion settings are in `config/production.json`.
- A new variable always has a safe default in code and goes into
  `.env.example`. Credentials (keys, passwords, URLs with credentials) never
  have a default value in code.
- A missing variable must not crash the pod. The staging and prod values go
  into the ConfigMap in `oke-staging/ai-platform/rag-system.yaml` /
  `oke-prod/ai-platform/rag-system.yaml`; that change is made by the
  platform team, so list the variable and its value in the MR description.
- Postgres (`src/adapters/postgres/`, asyncpg) already holds conversations,
  feedback, trace logs and bulk jobs. Extend that adapter instead of opening
  a new pool. Do not add new databases, queues or caches without the platform
  team.
- Weaviate collections are managed through `src/interfaces/manage.py` and
  `scripts/`. Never drop or recreate collections in staging or prod from code.

## 11. Git

- Small commits, one logical change each, with conventional prefixes
  (`feat:`, `fix:`, `test:`, `refactor:`, `chore:`).
- **Never** add `Co-Authored-By:` or any AI attribution.
- **Never commit `CLAUDE.md`** (here `*.md` is already gitignored).
- No emojis in code, comments, docs or commit messages.

## 12. References

| Topic | Where |
|---|---|
| Template overview, common FAQ (LLM keys, no-DB services) | `agenas-python-template/README.md` |
| Logging and privacy rules, Loki pipeline | `agenas-python-template/docs/LOGGING_AND_PRIVACY.md` |
| Integration guide (Italian, for people) | `agenas-python-template/docs/AI_PLATFORM_INTEGRATION.md` |
| Deploy handover (what the platform team provisions) | `agenas-python-template/docs/DEPLOY_HANDOVER.md` |
| LLM adapter and gateway client | `src/adapters/llm/__init__.py`, `src/adapters/llm/openai.py` |
| PII gate | `src/adapters/preprocessing/pii_blocker.py`, `self_hosted_pii.py`; `src/domain/errors.py` |
| User-text redaction | `src/utils/log_user_input.py` |
| Query orchestration | `src/interfaces/mod.py` |
| API, lifespan, routers | `src/interfaces/api.py` |
| Feature flags, env loading | `src/utils/env.py` |
| Testbook | `evals/README.md`, `testbook/run.py`, `testbook/mapping.csv` |
| Gateway API and data residency | `llm-gateway/app/server.py`, `llm-gateway/GDPR_COMPLIANCE.md` |
| Staging / prod configuration | `oke-staging/ai-platform/rag-system.yaml`, `oke-prod/ai-platform/rag-system.yaml` |
| KPI log contract | `KPI-dashboard/documentation/loki-query-inventory.md` |
