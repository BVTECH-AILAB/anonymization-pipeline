# agenas-python-template

Punto di partenza per i nuovi servizi Python della piattaforma MIA (AGENAS).
Contiene:

- `CLAUDE.md`: il contesto per Claude Code (architettura, stack, test,
  logging, privacy, git, deploy). È valido anche come guida per le persone.
- Un microservizio FastAPI funzionante con Postgres 17, già conforme alle
  regole: logging con redazione dei dati personali, health/readiness,
  migrazioni Alembic, errori in italiano, test unitari e di integrazione.
- `docs/`: logging e privacy, scheda di deploy, guida per sviluppare dentro ai-platform e `docs/ai-platform/CLAUDE.md`, il CLAUDE.md da usare quando si lavora su ai-platform.

## Creare un nuovo servizio

```bash
cp -r agenas-python-template ../mio-servizio
cd ../mio-servizio
bash scripts/init_project.sh mio-servizio MIOSVC
```

Lo script rinomina pacchetto (`mio_servizio`), immagine, chiave Sonar e
prefisso delle variabili (`MIOSVC_DATABASE_URL`...), rigenera `uv.lock` e
aggiunge `CLAUDE.md` al `.gitignore`.

> `CLAUDE.md` è versionato **solo in questo template**, perché è ciò che
> distribuiamo. Nei repository dei servizi resta locale e non si committa
> mai, come in tutti i repository AGENAS.

Se l'evolutiva va sviluppata dentro ai-platform, non serve creare un
servizio: leggi `docs/AI_PLATFORM_INTEGRATION.md` e copia `docs/ai-platform/CLAUDE.md`
nella root del tuo clone di ai-platform.

## Primo avvio

Requisiti: `uv`, `just`, Docker con compose.

```bash
cp .env.example .env
uv sync
just test                 # unit test
docker compose up --build # servizio + Postgres 17, migrazioni applicate all'avvio
curl localhost:8000/readyz
just test-int             # integration test sul Postgres del compose
```

OpenAPI su `http://localhost:8000/docs` (disattivata in prod).

## Comandi

| Comando | Cosa fa |
|---|---|
| `just up` / `just down` / `just logs` | Stack compose |
| `just dev` | API sull'host, DB nel compose |
| `just lint` | ruff fix + format + ty: prima di ogni commit |
| `just check` | Come la CI: nessuna correzione automatica |
| `just test` / `just test-cov` | Unit test / con coverage (minimo 80%) |
| `just test-int` | Integration test su Postgres |
| `just revision "msg"` / `just migrate` | Nuova migrazione / applica |
| `just audit` | Vulnerabilità nelle dipendenze |

## Struttura

```
src/anonymization_pipeline/
  main.py           create_app(), lifespan, entry point
  config.py         Settings (pydantic-settings, prefisso ANONPIP_)
  logging_setup.py  setup_logging, log_event, request id
  privacy.py        hash/mask del codice fiscale, redazione testo utente, filtro di log
  middleware.py     X-Request-ID
  errors.py         errori di dominio, handler, messaggi in italiano
  api/              router (health + esempio items)
  services/         logica di business
  db/               engine, sessioni, modelli
migrations/         Alembic (lock advisory per repliche multiple)
tests/unit          senza DB
tests/integration   con Postgres (marker integration)
```

L'esempio `items` serve solo a mostrare i pattern: sostituiscilo con il
tuo dominio (modello, migrazione, router, service, test).

## Casi frequenti

### Non mi serve il database

Va benissimo: molti servizi sono stateless. Togli la parte DB invece di
lasciarla inutilizzata:

1. Elimina `src/anonymization_pipeline/db/`, `migrations/`, `alembic.ini`,
   `scripts/db-init/` e `tests/integration/`.
2. Elimina l'esempio `items` (`api/items.py`, `services/items.py`, i relativi test).
3. In `pyproject.toml` rimuovi `sqlalchemy`, `asyncpg` e `alembic`, poi `uv lock`.
4. In `config.py` rimuovi i campi `database_url` e `db_*`. In `main.py` togli
   engine e sessionmaker dal `lifespan`.
5. `/readyz`: fallo controllare le dipendenze che usi davvero (es. llm-gateway),
   oppure fallo rispondere come `/healthz`. L'endpoint deve restare, perché
   le probe Kubernetes lo usano.
6. In `docker-compose.yml` togli il servizio `db` e il `depends_on`.
   In `Dockerfile` togli le `COPY` di `alembic.ini` e `migrations`.
   In `scripts/entrypoint.sh` togli il blocco delle migrazioni.
7. In `.gitlab-ci.yml` togli il job `integration-tests`, e nel `justfile` le
   ricette `db-shell`, `migrate`, `revision`, `downgrade` e `test-int`.
8. In `docs/DEPLOY_HANDOVER.md` segna "Serve un database? no": il team
   piattaforma non creerà il cluster Postgres.

Se più avanti il DB serve, ripristina questi file dal template.

### Mi servono gli LLM

Non si chiamano mai direttamente i provider (OpenAI, Gemini, Bedrock...).
Si passa sempre da **llm-gateway**, che gestisce instradamento, fallback,
residenza dei dati in UE e tracciamento dei costi per applicazione.

llm-gateway espone un'API **compatibile OpenAI** (`POST /chat/completions`,
`POST /responses`), quindi si usa il client `openai` puntato sul gateway,
come fa ai-platform in `src/adapters/llm/openai.py`:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url=settings.llm_gateway_base_url,
    api_key=settings.llm_gateway_api_key,
)
response = await client.chat.completions.create(
    model=settings.llm_gateway_model,  # alias del gateway, non il nome del provider
    messages=[{"role": "user", "content": testo_anonimizzato}],
)
```

Cosa aggiungere:

- **Dipendenza**: `uv add openai`.
- **Settings**, con le stesse regole delle altre variabili (`config.py`,
  `.env.example`, `docker-compose.yml`):

  | Variabile | Default in `config.py` | Note |
  |---|---|---|
  | `ANONPIP_LLM_GATEWAY_BASE_URL` | `""` | In cluster: `http://llm-gateway-app.llm-gateway.svc.cluster.local:8000` |
  | `ANONPIP_LLM_GATEWAY_API_KEY` | `""` | Credenziale: nessun default. Arriva da Vault |
  | `ANONPIP_LLM_GATEWAY_MODEL` | `fast-production` | Alias del gateway: `fast-production` o `strong-production` |

- **Il client** si crea nel `lifespan` e si salva su `app.state`, come
  l'engine del DB. Non va creato a ogni richiesta.
- **La chiave API (token) per llm-gateway** è per applicazione. Per
  ottenerla scrivi a **Pasqualino**, con **Maurizio Di Pierro** in cc,
  indicando il nome del servizio e l'ambiente (staging, prod). La chiave di
  prod viene messa in Vault e non passa dagli sviluppatori. Per lo sviluppo
  locale ricevi una chiave di staging da tenere solo nel tuo `.env`: non va
  condivisa, né incollata in chat o ticket, né committata.
- **In locale** punta `ANONPIP_LLM_GATEWAY_BASE_URL` al gateway di staging,
  raggiungibile dalla rete interna AGENAS.
- **Nei test** non si chiama mai il gateway vero: sostituisci il client con
  un fake tramite `app.dependency_overrides` o `monkeypatch`.
- **`/readyz`** può includere un controllo leggero sul gateway (`GET /health`).
- **Errori**: se il gateway non risponde, solleva `ServiceUnavailableError`
  (503) con un messaggio in italiano. Non inventare una risposta di ripiego.

### Il testo dell'utente arriva all'LLM

Se un testo scritto da un medico (domanda, nota, messaggio) finisce in un
prompt, prima deve passare da **ner-anonymizer**, che individua i dati
personali. Il comportamento è **fail-closed**: se il servizio NER non
risponde, la richiesta si rifiuta (503) e non si inoltra all'LLM. Il
riferimento è l'implementazione di ai-platform, descritta in
`docs/AI_PLATFORM_INTEGRATION.md`, sezione "Gate PII".

Prompt e risposte si possono loggare solo per il debug in staging, tramite
`redact_user_text()` (vedi `docs/LOGGING_AND_PRIVACY.md`).

### Mi serve una cache, una coda, un bucket o un altro servizio

Non aggiungerli da solo ai manifest né ai repo Flux. Usali in locale nel
`docker-compose.yml` per sviluppare, poi segnalali nella scheda
`docs/DEPLOY_HANDOVER.md`: il provisioning in staging e prod lo fa il team
piattaforma.

### Mi serve sapere chi è l'utente

L'identità arriva già validata a monte da WSO2 API Manager e da authz. Non
implementare la validazione dei JWT né una tabella utenti tua. Concorda col
team quali informazioni ti servono (regione, ASL, ruoli) e come arrivano
(header inoltrati, chiamata ad authz). Se devi salvare un identificativo,
usa `hash_identifier()` e mai il codice fiscale in chiaro.

### L'evolutiva va dentro ai-platform

Allora non serve un nuovo servizio: leggi `docs/AI_PLATFORM_INTEGRATION.md`
e copia `docs/ai-platform/CLAUDE.md` nella root del tuo clone di ai-platform.
Quel file dà a Claude Code il contesto di ai-platform.

## CI/CD

`.gitlab-ci.yml` segue lo schema degli altri servizi AGENAS: lint, tipi,
unit e integration test bloccanti, Sonar, build su Harbor
(`YYYY.MM.DD-test.<epoch>` da `test`, `YYYY.MM.DD` da `main`) e report CVE.
Il rilascio in staging e prod è gestito da Flux: vedi `docs/DEPLOY_HANDOVER.md`.
