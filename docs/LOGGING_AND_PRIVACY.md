# Logging e privacy

La piattaforma MIA tratta dati sanitari di cittadini italiani. Un log è un
trattamento di dati: finisce in Loki, viene conservato e lo leggono persone
diverse da chi ha scritto il codice. Le regole sono due:

- **nulla che identifichi una persona**, in nessun ambiente;
- **il testo dell'utente, i prompt e le risposte LLM** solo per il debug in
  local e staging, mai in produzione.

## Come funziona la pipeline

```
servizio -> stdout (testo) -> Grafana Alloy -> Loki -> Grafana / KPI-dashboard
```

- Alloy aggiunge da solo le label `namespace`, `pod`, `container`, `app`
  (da `app.kubernetes.io/name`), `job` e `cluster`. In Loki compare anche
  `service_name`, ricavato dal nome del deployment: è il selettore che usa
  KPI-dashboard (es. `{service_name="rag-system"}`).
- Non servono configurazioni lato servizio: basta scrivere su stdout.
- Non scrivere file di log, non usare handler esterni (Logtail, PostHog e
  simili), non usare `print()` (vietato da ruff, regola T20).

## Formato

```
2026-10-06 10:00:00,123 INFO anonmyzation_pipeline.services.jobs [3f2a9c...]: anonymization_job_created {"job_id": "9ab85d97...", "pages": 2, "size_kb": 248, "source_format": "pdf", "mode": "accurate"}
```

`%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s`

- `request_id` arriva dall'header `X-Request-ID`, se valido, oppure viene
  generato. Viene restituito nella risposta: chiedilo a chi segnala un errore
  e cercalo in Grafana.
- Fuori da una richiesta HTTP vale `-`.

## Livelli

| Livello | Quando | Esempio corretto | Esempio sbagliato |
|---|---|---|---|
| DEBUG | Dettaglio diagnostico. Solo local e staging: in prod è forzato a INFO | `"Prompt: %s", redact_user_text(prompt, settings)` | `"Prompt: %s", prompt` |
| INFO | Avvio e arresto, eventi di business senza contenuto | `log_event(logger, "anonymization_completed", {"job_id": job_id, "pages": 2})` | `"Utente %s ha scritto %s"` |
| WARNING | Degradazione gestita: retry, fallback, dipendenza giù | `"NER unavailable, request refused (attempt %d)"` | logger.warning per ogni richiesta valida |
| ERROR | La richiesta è fallita. Passa `exc_info=` per avere il traceback | `logger.error("Unhandled %s on %s", type(exc).__name__, path, exc_info=exc)` | `logger.error(str(exc))` con il body della richiesta |

In staging `ANONPIP_LOG_LEVEL=DEBUG` è ammesso. In prod il servizio lo
ignora e usa INFO.

## Cosa non si logga mai (in nessun ambiente)

- Codici fiscali, nomi, cognomi, email, telefoni, date di nascita.
- JWT, API key, token, password, connection string del DB.
- Header grezzi (`Authorization`, `X-JWT-Assertion`, `X-Api-Key`...) e body
  grezzi di richieste e risposte.
- Il campo `input` degli errori di validazione: l'handler 422 lo rimuove già.
- Messaggi di eccezione dei driver, che possono contenere host, utente o dati.
  Logga `type(exc).__name__`.

## Cosa si logga solo per il debug (mai in produzione)

- Testo inserito dall'utente: domande, messaggi di chat, note, campi liberi.
- Prompt inviati all'LLM e risposte generate.

In local e staging questi contenuti servono per capire perché una risposta
è sbagliata. Si possono loggare solo se valgono tutte e tre le condizioni:

1. passano da `redact_user_text(text, settings)`, mai direttamente;
2. sono a livello `DEBUG`;
3. è attivo `ANONPIP_EXTENDED_LOG_CONTEXT=true`, che di default è spento.
   Si accende per il tempo dell'indagine e poi si rispegne.

```python
logger.debug("LLM prompt: %s", redact_user_text(prompt, settings))
logger.debug("LLM response: %s", redact_user_text(answer, settings))
```

In produzione resta tutto strict senza eccezioni: con
`ANONPIP_ENVIRONMENT=prod` il flag viene forzato a `false` e DEBUG a INFO,
quindi `redact_user_text` restituisce sempre `<redacted>` anche se qualcuno
lascia il flag acceso nella ConfigMap. In ogni ambiente i codici fiscali
dentro il testo restano mascherati.

## Strumenti (`privacy.py`)

| Funzione | Uso |
|---|---|
| `hash_identifier(cf)` | SHA256 su `strip().upper()`, stessa normalizzazione di authz: valori confrontabili tra servizi. Per log e DB. |
| `mask_identifier(cf)` | `R***U`, per log leggibili da un umano. |
| `redact_user_text(text, settings)` | Per testo utente, prompt e risposte LLM. Restituisce `<redacted>`, tranne in local e staging con `ANONPIP_EXTENDED_LOG_CONTEXT=true`: lì restituisce il testo, con i codici fiscali comunque mascherati. In prod è sempre `<redacted>`. |
| `PiiRedactingFilter` | Installato da `setup_logging` sull'handler di stdout: maschera i codici fiscali in messaggi, argomenti e traceback, inclusi i log di uvicorn. È una rete di sicurezza, non un permesso. |

`ANONPIP_EXTENDED_LOG_CONTEXT` corrisponde a `EXTENDED_LOG_CONTEXT` di
ai-platform (`src/utils/log_user_input.py::redact_query`).

## Eventi per le dashboard (contratto con KPI-dashboard)

KPI-dashboard legge Loki filtrando per stringhe ed estraendo JSON dalle
righe. Esempi dei formati esistenti:

| Servizio | Riga | Usata per |
|---|---|---|
| authz | `{"event": "access", "doctor_id": "<hash[:5]>", "authorized": true, "roles": [...], "regione": ..., "asl": ..., "application": "MIA", ...}` | Accessi, utenti unici, coorti |
| rag-system | `conversation: {json}` con `thread_uuid`, `regione`, `asl`, `conversation_type`... | Conversazioni |
| rag-system | `inference_latency {json}` con `client_ttft_ms` | Latenza |
| rag-system | `Hallucination detection: level=...` | Allucinazioni |
| rag-system | access log uvicorn `"POST /chat/stream HTTP/1.1" 200` | Success rate, uptime |
| anonmyzation-pipeline | `anonymization_job_created {json}` (`job_id`, `pages`, `size_kb`, `source_format`, `mode`) | Documenti caricati, per modalità |
| anonmyzation-pipeline | `anonymization_completed {json}` (`job_id`, `pages`, `scanned_pages`, `boxes`, `values_by_label`, `verified`, `edits`, `seconds_total`, `timings`, `mode`) | Volumi, dati oscurati per tipo, verifiche, tempi |
| anonmyzation-pipeline | `anonymization_edited {json}`, `anonymization_failed {json}`, `anonymization_cancelled {json}`, `anonymization_job_deleted {json}` | Correzioni manuali, errori, cancellazioni |

Inventario completo: `KPI-dashboard/documentation/loki-query-inventory.md`.

Regole per i nuovi eventi:

1. Usa `log_event(logger, "nome_evento", {...})`: produce `nome_evento {json}`.
2. Nome in snake_case, stabile nel tempo. Le chiavi del payload pure.
3. Nel payload vanno solo contatori, id tecnici, hash, enum e durate. Mai
   testo utente, mai identificativi in chiaro.
4. Rinominare un evento o una chiave rompe le dashboard senza errori
   visibili. Ogni evento ha un test che ne verifica nome e chiavi, e una
   modifica va concordata con chi mantiene KPI-dashboard.

Nel servizio di anonimizzazione nessuna riga contiene testo del documento,
valori rilevati o nomi dei file: solo id del job, contatori e tempi (vedi
`docs/ANONYMIZATION.md`, test in `apps/api/tests/unit/test_logging.py`).

## Test di non-leak

Ogni funzionalità che tocca testo utente o identificativi ha un test che
passa dall'handler di produzione e verifica che il dato non compaia:

```python
def test_feature_never_logs_user_text(client, log_stream):
    client.post("/my-endpoint", json={"text": f"paziente {FAKE_CF} con febbre"})

    logs = log_stream.getvalue()
    assert "febbre" not in logs
    assert FAKE_CF not in logs
```
