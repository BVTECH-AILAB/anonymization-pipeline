# Sviluppare dentro ai-platform

Non tutte le evolutive diventano un nuovo servizio. Se la funzionalità fa
parte del flusso RAG (un nuovo step di retrieval, un pre/post-processore,
un nuovo endpoint dell'API di chat) va sviluppata **dentro ai-platform**,
seguendo le sue convenzioni e non il layout di questo template.

Prima di iniziare copia `docs/ai-platform/CLAUDE.md` di questo template
nella root del tuo clone di ai-platform:

```bash
cp agenas-python-template/docs/ai-platform/CLAUDE.md ai-platform/CLAUDE.md
```

Il `.gitignore` di ai-platform esclude `*.md`, quindi il file resta locale e
non viene committato. Dà a Claude Code il contesto di ai-platform: struttura,
comandi, standard di codice, gate PII, contratto dei log con KPI e
riferimenti ai file.

## Nuovo servizio o modulo di ai-platform?

| Nuovo servizio (questo template) | Modulo di ai-platform |
|---|---|
| Ha dati e ciclo di vita propri (DB, job, API separate) | Si inserisce nel percorso domanda -> risposta |
| Può essere scalato o rilasciato in autonomia | Usa Weaviate, i chunk, il thread di conversazione |
| Chiamato da più client (fe, altri servizi) | Chiamato solo dalla pipeline RAG |
| Esempi: un servizio di notifiche, un catalogo esterno | Esempi: nuovo reranker, nuovo guardrail, routing per campagna |

In caso di dubbio, chiedi prima di creare un nuovo repository.

## Ciclo di sviluppo

Ogni modifica parte da un branch dedicato, mai da commit diretti su `test` o `main`.

1. **Crea il branch**, quasi sempre da `main` (lo stato attualmente in produzione):
   ```bash
   git checkout main && git pull
   git checkout -b feature/<nome-breve>
   ```
2. **Sviluppa e verifica in locale**: `just lint` e `just test-fast` devono passare.
3. **MR del branch verso `test`**. Il merge su `test` fa partire la CI, che
   costruisce l'immagine `YYYY.MM.DD-test.<epoch>`, e Flux la rilascia in **staging**.
4. **Prova in staging** la funzionalità end-to-end, inclusi i log in Grafana
   (niente testo utente, righe KPI invariate).
5. **MR da `test` verso `main`**, solo dopo la validazione in staging. Il
   merge su `main` costruisce `YYYY.MM.DD` e Flux la rilascia in **produzione**.

Gli unici merge ammessi sono:

- feature branch -> `test` (rilascio in staging);
- `test` -> `main` (rilascio in produzione).

Un feature branch non si mergia mai direttamente in `main`. Il merge
`test` -> `main` porta in produzione tutto ciò che è in `test`, quindi in
`test` deve esserci solo codice pronto per essere validato in staging.

## Dove va il codice

| Cosa | Dove | Pattern |
|---|---|---|
| Client verso un sistema esterno | `src/adapters/<area>/` | Protocol + implementazioni + factory (`get_llm()`, `get_vectordb()`, `get_embedder()`, `get_preprocessor()`) |
| Step del flusso di query | `src/strategies/<nome>.py` | Un modulo per step (es. `guardrail.py`, `document_retrieval.py`) |
| Modelli ed errori di dominio | `src/domain/` | Pydantic in `chat.py`, eccezioni in `errors.py` con messaggi in italiano |
| Nuovi endpoint | `src/interfaces/<nome>.py` | `APIRouter` dedicato, montato in `api.py` con `include_router` dietro una feature flag (vedi `tracelogs_enabled()` in `src/utils/env.py`) |
| Risorse condivise | `lifespan` in `src/interfaces/api.py` | Helper `_connect_*` che salvano il client su `app.state` |
| Ingestion | `src/pipeline/` | Step, planner, executor |

## Regole specifiche di ai-platform

- Import con prefisso `src.` (es. `from src.adapters.llm import get_llm`),
  tutti a livello di modulo: niente import lazy.
- Mai `python` senza `uv run`.
- Type checker: `ty`. Prima di chiudere: `just lint`.
- Test: `just test-fast` (usa l'immagine già costruita). `just test`
  ricostruisce l'immagine ed è pesante: serve solo dopo un cambio di dipendenze.
- LLM: sempre `get_llm()` (`src/adapters/llm`), che passa da llm-gateway
  (`LLM_GATEWAY_BASE_URL`, `LLM_GATEWAY_API_KEY`). Mai SDK dei provider.
- Configurazione: `src/utils/env.py` e `config/production.json`. Una nuova
  variabile ha sempre un default e va aggiunta anche a `.env.example`.

## Gate PII: non va mai aggirato

Il testo dell'utente passa dal preprocessore PII (`src/adapters/preprocessing/`,
`pii_blocker.py` e `self_hosted_pii.py`) che chiama ner-anonymizer
**prima** di qualsiasi chiamata all'LLM. Il comportamento è fail-closed:

- NER irraggiungibile -> `NERServiceUnavailableError` -> HTTP 503 (o evento SSE `NER_UNAVAILABLE`).
- PII trovata -> `PIIDetectedError` -> HTTP 400 o messaggio in chat.

Ogni nuovo percorso che porta testo utente verso un LLM deve passare dallo
stesso gate. Non aggiungere fallback che inoltrano il messaggio se NER non
risponde.

## Logging dentro ai-platform

- Testo dell'utente, prompt e risposte LLM si loggano solo per il debug in
  staging: tramite `redact_query()` (`src/utils/log_user_input.py`), a livello
  DEBUG, con `EXTENDED_LOG_CONTEXT=true`. In produzione `EXTENDED_LOG_CONTEXT`
  deve restare spento.
- Le righe di log lette da KPI-dashboard sono un contratto, e non vanno
  modificate senza accordo:
  - il prefisso `[<thread_uuid>]` del formato di log;
  - `conversation: {json}` (emesso in `api.py`);
  - `inference_latency {json}` (`src/utils/latency.py`);
  - `Hallucination detection: level=...`;
  - l'access log di uvicorn per `POST /chat/stream` e `GET /`.
- Inventario completo: `KPI-dashboard/documentation/loki-query-inventory.md`.

## Checklist per una MR su ai-platform

- [ ] `just lint` e `just test-fast` passano.
- [ ] I nuovi endpoint sono dietro feature flag, con default che non cambia il comportamento attuale.
- [ ] Testo utente, prompt e risposte LLM solo via `redact_query()` a DEBUG, mai in chiaro (test di non-leak).
- [ ] Il gate NER copre il nuovo percorso.
- [ ] Le righe di log usate da KPI non sono cambiate.
- [ ] Le nuove variabili d'ambiente hanno un default e sono in `.env.example`.
