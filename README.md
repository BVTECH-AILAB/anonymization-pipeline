# anonymization-pipeline

Servizio di anonimizzazione di documenti italiani (soprattutto sanitari): PDF
nativi e scansionati, immagini e testo. Tutto gira in locale: un modello
visione-linguaggio (VLM) su Ollama, docling per le pagine native dei PDF e
rilevatori su CPU (regole con checksum, etichette, NER). Il risultato è il
Markdown del documento censurato, con un report e, in modalità pseudonimo,
una mappa cifrata che permette di ripristinare i valori originali.

Il servizio segue `agenas-python-template` (vedi `CLAUDE.md`), con le
deroghe descritte in `docs/DEPLOY_HANDOVER.md`: niente llm-gateway (il VLM è
locale e i documenti non escono mai dal perimetro), niente database, lavori
asincroni in memoria. Il codice viene da `anonimizzatore-vlm`, portato nel
template e tradotto in inglese; categorie, tag (`[PERSONA_1]`) e formato
della mappa sono invariati.

## Come funziona

```
file -> ingest -> estrazione -> rilevamento -> censura -> verifica -> Markdown censurato
          |          |              |               |           |
       pagine     docling (PDF   regole, etichette, tag coerenti il VLM rilegge il testo censurato
       (PDF a     nativi) o VLM  NER e VLM in       per forma   con il Markdown in chiaro e cerca
       200 dpi)   (scansioni)    unione             esatta      dati rimasti in chiaro (fail-closed)
```

1. **Ingest** (`ingest/`): PDF, immagini (png, jpg, tif, bmp, webp) o testo.
   Ogni pagina PDF viene renderizzata; una pagina è "nativa" se ha testo
   leggibile e visibile (niente testo nascosto, poche immagini).
2. **Estrazione** (`extraction/`): pagine native con docling (testo esatto,
   tabelle), tutte le altre con il VLM, una chiamata per pagina.
3. **Rilevamento** (`detection/`, `detectors/`): rilevatori in unione, basta
   che uno segnali un valore. Ogni valore deve comparire davvero nel testo,
   altrimenti è un'allucinazione e si scarta. Un filtro comune ignora gli
   eponimi, le scale e i farmaci ("morbo di Parkinson", "Glasgow 15",
   "Coumadin") decidendo dal contesto di ogni occorrenza: un paziente che si
   chiama Parkinson viene censurato.
4. **Censura** (`redaction/`): i valori con la stessa forma (a meno di spazi
   e maiuscole) nella stessa famiglia di categorie hanno lo stesso tag ("Mario
   Rossi" e "MARIO ROSSI" sì, "Rossi Mario" no); la sostituzione è meccanica e
   vale per tutto il documento. Modalità `pseudonym` (tag + mappa
   AES-256-GCM), `tag` o `redaction` (simbolo fisso).
5. **Verifica** (`verification/`): il VLM rilegge il Markdown censurato di
   ogni pagina confrontandolo con il Markdown originale in chiaro (non
   riceve l'immagine), una sola volta. Modalità `off` (nessun controllo),
   `report` (i residui vanno nel report con categoria, pagina e posizione
   `start`/`end` nel Markdown censurato, mai il valore; il documento viene
   restituito) o
   `block` (default: con residui il documento viene rifiutato, HTTP 422,
   mai valori nella risposta). Se la verifica non è possibile il documento
   viene rifiutato.

## Requisiti

- Docker con compose, `uv`, `just`.
- Una GPU NVIDIA con NVIDIA Container Toolkit (su WSL2: Docker Desktop con
  backend WSL2), con VRAM sufficiente per i modelli del file dei modelli
  (`resources/models.yaml`, o il proprio in `./config` indicato da
  `ANONPIP_MODELS_FILE=/app/config/<file>.yaml`). Su sola CPU funziona, ma
  solo per prove.

## Primo avvio

```bash
cp .env.example .env
uv sync
just test                 # unit test (nessuna rete, VLM finto)
just models               # unico passo con internet: i VLM del file dei modelli, NER e docling
just up-gpu               # oppure `just up` (solo CPU)
curl localhost:8000/readyz
```

- API: `http://localhost:8000` (OpenAPI su `/docs`, disattivata in prod).
- Interfaccia web: `http://localhost:8080`.
- `just test-vlm`: test di integrazione contro l'Ollama del compose.

### Isolamento

A runtime il servizio non ha accesso a internet:

- `ollama` è solo sulla rete `internal` (`internal: true`), senza porte
  pubblicate;
- `app` raggiunge solo URL interni, legge i modelli NER e docling da volumi
  in sola lettura con `HF_HUB_OFFLINE=1`, e non chiede mai a Ollama di
  scaricare modelli (`ANONPIP_VLM_PULL_MISSING=false`);
- le porte sono pubblicate solo su `127.0.0.1`.

Solo il profilo `models` (`just models`) usa la rete `egress`, per
scaricare i modelli. Nel cluster le stesse regole sono NetworkPolicy
(`docs/DEPLOY_HANDOVER.md`, `docs/k8s-example/service.yaml`).

## API

| Metodo e percorso | Cosa fa |
|---|---|
| `GET /healthz`, `GET /readyz`, `GET /version` | Probe; `/readyz` controlla il backend VLM e il modello (503 se mancano) e riporta lo stato di NER e docling |
| `POST /anonymize` | Anonimizza un documento: ZIP (default) o `?format=json` |
| `POST /restore`, `POST /restore/detail` | Ripristina i valori da Markdown + mappa (`/detail`: con le posizioni dei tag) |
| `POST /restore/pdf` | Ricostruisce il PDF originale da PDF censurato con layout (o la sua struttura `.anon.docling.json`) + mappa |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/result`, `DELETE /jobs/{id}` | Elaborazione asincrona (usata dall'interfaccia web) |
| `GET /models`, `GET /categories`, `GET /configuration` | Modelli nel backend, categorie, configurazione effettiva (senza segreti) |

Campi multipart di `/anonymize` e `/jobs`: `file`, `mode`
(`pseudonym`, `tag`, `redaction`), `verification` (`off`, `report`, `block`),
`extraction` (`docling`, `vlm`), `model_extraction`, `model_detection`,
`model_verification` (uno di `GET /models`), `detectors`,
`enable`/`disable` (categorie o gruppi separati da virgola), `key` (oppure
header `X-Anon-Key`; senza, vale `ANONPIP_VAULT_KEY`), `layout` (anche il
PDF censurato con il layout dell'originale; senza, vale
`ANONPIP_DEFAULT_LAYOUT_OUTPUT`), `transcript` (solo `/jobs` e solo con
`ANONPIP_ALLOW_TRANSCRIPT=true`: trascrizione in chiaro per il confronto
nell'interfaccia).

```bash
curl -F file=@tests/fixtures/ricetta.txt -F mode=tag "localhost:8000/anonymize?format=json"
```

Output (anche nello ZIP): `<nome>.anon.md`, `<nome>.report.json`,
`<nome>.metriche.json`, in modalità pseudonimo `<nome>.mappa.enc`. Report e
metriche contengono solo conteggi, categorie, pagine e tempi.

Con `layout` anche `<nome>.anon.pdf` e `<nome>.anon.docling.json`. Il PDF
censurato ricalca l'originale a partire dagli oggetti docling (posizioni,
testo, tabelle; le immagini diventano un riquadro `[IMMAGINE]`, le pagine
estratte dal VLM sono testo a flusso) e ha allegata la struttura docling
censurata (il JSON: tag al posto dei valori, senza immagini, metadati e
nome del file). In modalità pseudonimo la mappa contiene anche dove stanno i
valori nella struttura: `POST /restore/pdf` con PDF censurato, mappa e
chiave restituisce il PDF originale ricostruito (stessi testi e posizioni,
font approssimato, senza immagini). Se la struttura o il PDF potessero
contenere testo che il Markdown censurato non contiene, i due file non
vengono prodotti (avviso `layout_unavailable`, report `layout:
unavailable`): il Markdown resta l'output di riferimento.

## Configurazione

- Variabili `ANONPIP_*` (`config.py`, `.env.example`, elenco completo in
  `docs/DEPLOY_HANDOVER.md`): backend VLM, modalità predefinite,
  chiave della mappa, limiti e lavori asincroni.
- Modelli VLM: un file YAML separato,
  `src/anonymization_pipeline/resources/models.yaml`, oppure quello indicato
  da `ANONPIP_MODELS_FILE` (in compose `./config` è montata su
  `/app/config`). Contiene il catalogo (`models`: nome e contesto di ogni
  modello, e se accetta immagini: `vision`, da `ollama show <modello>`) e il
  modello predefinito di ogni fase (`phases`: estrazione,
  rilevamento, verifica). `just models` scarica tutto il catalogo;
  a runtime ogni richiesta può scegliere, fase per fase, un modello del
  catalogo presente nel backend (campi `model_extraction`,
  `model_detection`, `model_verification`; elenco in
  `GET /models`). Con `OLLAMA_MAX_LOADED_MODELS=1` modelli diversi fra le
  fasi vengono scambiati in VRAM a ogni cambio di fase.
- docling (pagine native dei PDF): un file YAML separato,
  `src/anonymization_pipeline/resources/docling.yaml`, con parser PDF,
  modello di layout, modello e modalità delle tabelle, cell matching, thread
  e timeout. `ANONPIP_DOCLING_FILE` indica un file con le sole chiavi da
  cambiare (in compose, in `./config`). `just models` scarica i modelli di
  layout e tabelle scelti nel file, quindi il file deve essere lo stesso
  anche per il download.
- Pipeline (passate del rilevamento, rilevatori accesi, soglie, timeout):
  `src/anonymization_pipeline/resources/default.yaml`, con i prompt in
  `resources/prompts/<fase>.yaml`. Si cambia con un file YAML parziale
  indicato da `ANONPIP_PIPELINE_CONFIG_FILE`; le chiavi possedute dalle
  variabili `ANONPIP_*`, dal file dei modelli e dal file docling non sono
  ammesse nel file
  (l'elenco è nell'intestazione di `default.yaml`).
  Ogni chiave sconosciuta è un errore e il servizio non parte.
- Categorie e gruppi: `resources/categories.yaml`, l'unico elenco. Sono
  tutte attive: `default.yaml` elenca solo quelle spente.
- Vocabolario dei rilevatori di testo, in `resources/detectors/`:
  `rules.yaml` (regex e checksum), `labels.yaml` (etichetta -> valore),
  `ner.yaml` (etichette del modello NER -> categorie, filtri),
  `eponyms.yaml`. Le regex hanno la stessa sintassi in tutti i file
  (frammenti `${nome}`, liste come alternative); i file vengono validati
  all'avvio e non si sovrascrivono dal file utente.

## Comandi

| Comando | Cosa fa |
|---|---|
| `just models` | Scarica nei volumi tutti i VLM del file dei modelli, NER e docling (unico passo con internet) |
| `just up` / `just up-gpu` / `just down` / `just logs` | Stack compose (CPU / GPU NVIDIA) |
| `just dev` | API sull'host (serve un backend VLM raggiungibile: `ANONPIP_VLM_URL`) |
| `just lint` | ruff fix + format + ty: prima di ogni commit |
| `just check` | Come la CI: nessuna correzione automatica |
| `just test` / `just test-cov` | Unit test / con coverage (minimo 80%) |
| `just test-vlm` | Test di integrazione con il VLM reale (nel container `app`) |
| `just audit` | Vulnerabilità nelle dipendenze |
| `just docs-pdf` | Rigenera il PDF di documentazione tecnica (`docs/architettura-anonymization-pipeline.pdf`) |
| `just validation-dataset` | Rigenera PDF e ground truth del dataset sintetico di validazione dalle spec |
| `just validation` | Validazione sul dataset sintetico: profili di `validation/config.yaml`, report Markdown con le metriche |

Gli extra opzionali `ner` (torch CPU + transformers) e `docling` sono
nell'immagine Docker; in locale `uv sync --all-extras` li installa, e
`uv run pytest -m "ner or docling"` esegue i test che usano i modelli veri.

## Struttura

```
src/anonymization_pipeline/
  main.py             create_app(), lifespan (client HTTP, service, lavori su app.state)
  config.py           Settings (pydantic-settings, prefisso ANONPIP_)
  api/                router: health, anonymization, jobs, catalog
  services/           anonymization (slot, errori, stato), jobs (coda in memoria), results
  pipeline.py         orchestrazione delle fasi
  pipeline_config/    schema, caricamento a strati, registro delle categorie
  resources/          default.yaml, models.yaml, categories.yaml, prompts/, detectors/, fonts/
  ingest/ extraction/ detection/ detectors/ redaction/ verification/ vlm/
  rendering.py        PDF con il layout dalla struttura docling (reportlab)
  domain.py text.py metrics.py model_store.py
  logging_setup.py privacy.py middleware.py errors.py
frontend/             interfaccia web React + Vite, servita da nginx
tests/unit            senza rete (VLM finto); tests/integration con il VLM reale
tests/fixtures        documenti sintetici
validation/           dataset sintetico di validazione, prompt di generazione, harness e report
```

La validazione della qualità (recall, fughe, precision per categoria, tipo
e difficoltà, confronto tra configurazioni) è descritta in
[validation/README.md](validation/README.md).

## Privacy

Vedi `docs/LOGGING_AND_PRIVACY.md`. In sintesi: nei log solo l'impronta del
documento (SHA256 troncato), mai nome del file, testo, valori, prompt o
risposte del VLM; errori in italiano senza eco dell'input; la trascrizione
in chiaro esiste solo su richiesta e mai in prod.

## CI/CD

`.gitlab-ci.yml`: lint, tipi, unit test e test del frontend bloccanti,
Sonar, build su Harbor delle immagini `anonymization-pipeline` e
`anonymization-pipeline-web` (`YYYY.MM.DD-test.<epoch>` da `test`,
`YYYY.MM.DD` da `main`) e report CVE. Il rilascio è gestito da Flux: vedi
`docs/DEPLOY_HANDOVER.md`.
