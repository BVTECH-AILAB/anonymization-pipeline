# Deploy handover

Chi sviluppa il servizio **non modifica** i repository Flux (`oke-staging`,
`oke-prod`). Compila questa scheda e la consegna al team piattaforma, che
fa il provisioning.

## Cosa consegna lo sviluppatore

| Voce | Valore |
|---|---|
| Nome servizio / immagini | `anonymization-pipeline` -> `harbor.agenas.oci/agenas/anonymization-pipeline`; interfaccia web `anonymization-pipeline-web` -> `harbor.agenas.oci/agenas/anonymization-pipeline-web` |
| Repository GitLab | `gitlab.agenas.oci:application/backend/anonymization-pipeline` |
| Namespace proposto | |
| Porta | API `8000` (nome `web`); interfaccia web `80` |
| Liveness probe | `GET /healthz` |
| Readiness probe | `GET /readyz` (controlla il backend VLM e la presenza del modello predefinito di ogni fase) |
| Serve un database? | **no** (servizio stateless; i lavori asincroni sono in memoria) |
| Esposizione | solo interna (`traefik-int`) / tramite WSO2 API Manager |
| Chi lo chiama | interfaccia web; altri servizi della piattaforma (da definire) |
| Chi chiama (dipendenze) | **solo** il backend VLM interno (Ollama, o un server compatibile OpenAI come vLLM) |
| Repliche | **1** (vedi "Lavori in memoria") |
| Risorse API | requests 2 CPU / 6Gi, limits 4 CPU / 10Gi (NER e docling girano su CPU nel pod) |
| Risorse backend VLM | 1 GPU NVIDIA da 24 GB (`nvidia.com/gpu: 1`), 16Gi di RAM, con il file dei modelli predefinito; con 8 GB di VRAM un file dei modelli con `gemma4:e4b-it-qat` (esempio in `config/models.8gb.yaml`) |
| Volumi | PVC modelli dell'API ~2Gi (NER ~450 MB in `/models/hf`, docling ~700 MB in `/models/docling`), montata in sola lettura; PVC modelli di Ollama: somma dei modelli del catalogo più margine (~40Gi con il catalogo predefinito, ~16 GB) |
| File dei modelli | facoltativo: ConfigMap con `models.yaml` montata nell'API e nel Job di download, indicata da `ANONPIP_MODELS_FILE` (vedi `docs/k8s-example/service.yaml`); senza, vale quello nell'immagine |
| Migrazioni all'avvio | nessuna |

### Deroghe al template (da approvare)

Il servizio *è* l'anonimizzatore, e il requisito è che i documenti non
escano mai dal perimetro. Per questo si discosta da `CLAUDE.md` in tre punti:

1. **Niente llm-gateway** (`CLAUDE.md` §1 e §7). Il VLM gira dentro il
   cluster (Ollama su GPU), quindi i dati non raggiungono nessun provider:
   il vincolo di residenza UE è rispettato a maggior ragione. Il client
   supporta anche le API compatibili OpenAI: un eventuale passaggio a
   llm-gateway (se servisse un modello visivo) richiederebbe solo
   `ANONPIP_VLM_BACKEND=openai`, `ANONPIP_VLM_URL` e `ANONPIP_VLM_API_KEY`.
2. **Niente gate ner-anonymizer**: il testo del documento va solo al VLM
   locale, che è parte della pipeline di anonimizzazione.
3. **Lavori in memoria** (`/jobs`): senza database un lavoro esiste solo nel
   pod che l'ha ricevuto e si perde a ogni riavvio. Con una replica non ci
   sono problemi; con più repliche serve la session affinity sull'Ingress
   (oppure una persistenza dei lavori, da progettare).

L'API non implementa un proprio token: l'identità arriva da WSO2.

### Rete (isolamento)

Il servizio deve funzionare senza accesso a internet. Unica eccezione: il
download iniziale dei modelli.

- `NetworkPolicy` dell'API: egress **solo** verso il backend VLM
  (porta 11434) e il DNS del cluster.
- `NetworkPolicy` del backend VLM: nessun egress; ingress solo dall'API.
- Download dei modelli: un `Job` una tantum (stessa immagine dell'API,
  comando `python -m anonymization_pipeline.model_store`, con
  `HF_HUB_OFFLINE=0` e `TRANSFORMERS_OFFLINE=0`) che riempie le PVC dei
  modelli; per i VLM serve un Ollama temporaneo con egress sulla stessa PVC,
  e il Job li scarica tutti: sono i modelli del catalogo del file dei
  modelli (`ANONPIP_MODELS_FILE`, la stessa ConfigMap dell'API, altrimenti
  quello dell'immagine). È l'unico componente a cui va concesso egress,
  e solo per la sua durata. In alternativa i modelli si copiano nelle PVC da
  un mirror interno.
- A runtime l'immagine non scarica nulla: `HF_HUB_OFFLINE=1` e
  `TRANSFORMERS_OFFLINE=1` sono impostati nel Dockerfile, e
  `ANONPIP_VLM_PULL_MISSING=false` impedisce al servizio di chiedere a
  Ollama un download.

### Variabili d'ambiente

Una riga per ogni campo di `Settings`. Segreto = arriva da Vault, non va
nella ConfigMap e nel codice non ha default.

| Variabile | Default | Staging | Prod | Segreto |
|---|---|---|---|---|
| `ANONPIP_ENVIRONMENT` | `prod` | `staging` | `prod` | no |
| `ANONPIP_LOG_LEVEL` | `INFO` | `DEBUG` | `INFO` | no |
| `ANONPIP_EXTENDED_LOG_CONTEXT` | `false` | `false` (solo per debug mirato) | ignorata | no |
| `ANONPIP_LOG_MASK_PREFIX` | `1` | | | no |
| `ANONPIP_LOG_MASK_SUFFIX` | `1` | | | no |
| `ANONPIP_VLM_BACKEND` | `ollama` | `ollama` | `ollama` | no |
| `ANONPIP_VLM_URL` | `http://ollama:11434` | URL del Service del backend VLM | idem | no |
| `ANONPIP_VLM_API_KEY` | nessuno (credenziale) | solo per backend `openai` che la richiedono | idem | **sì** |
| `ANONPIP_VLM_CONCURRENCY` | `2` | | | no |
| `ANONPIP_VLM_PULL_MISSING` | `false` | `false` | `false` | no |
| `ANONPIP_DEFAULT_MODE` | `pseudonym` | | | no |
| `ANONPIP_DEFAULT_VERIFICATION` | `block` | | | no |
| `ANONPIP_DEFAULT_EXTRACTION` | `docling` | | | no |
| `ANONPIP_DOCLING_MODELS_DIR` | `/models/docling` | | | no |
| `ANONPIP_PIPELINE_CONFIG_FILE` | vuoto | facoltativo (file montato da ConfigMap) | idem | no |
| `ANONPIP_MODELS_FILE` | vuoto (file dei modelli nell'immagine) | facoltativo (file montato da ConfigMap, lo stesso per API e Job di download) | idem | no |
| `ANONPIP_VAULT_KEY` | nessuno (credenziale) | facoltativa: senza, ogni richiesta in modalità pseudonimo porta la sua chiave | idem | **sì** |
| `ANONPIP_MAX_UPLOAD_MB` | `50` | | | no |
| `ANONPIP_MAX_CONCURRENT_JOBS` | `1` | | | no |
| `ANONPIP_JOB_WAIT_S` | `30` | | | no |
| `ANONPIP_MAX_QUEUED_JOBS` | `20` | | | no |
| `ANONPIP_MAX_JOBS` | `50` | | | no |
| `ANONPIP_JOB_TTL_S` | `3600` | | | no |
| `ANONPIP_ALLOW_TRANSCRIPT` | `false` | `true` solo se serve il confronto in chiaro nell'interfaccia | ignorata (forzata a `false`) | no |

Variabili dell'immagine (già nel Dockerfile, non vanno nella ConfigMap):
`HF_HOME=/models/hf`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`.

L'interfaccia web (nginx) fa da proxy di `/api/` verso l'API indicata da
`API_UPSTREAM` (variabile del container web, default `http://app:8000`, il
nome del servizio in compose; nel cluster:
`API_UPSTREAM=http://anonymization-pipeline:8000`). Il proxy ammette upload
fino a 101 MB: se si alza `ANONPIP_MAX_UPLOAD_MB` va alzato anche
`client_max_body_size` in `frontend/nginx.conf.template`.

## Cosa fa il team piattaforma

1. **Namespace**: cartella `<namespace>/` in `oke-staging` e `oke-prod` con `ns.yaml`.
2. **Database**: non serve.
3. **Backend VLM**: Deployment di Ollama (`ollama/ollama:0.35.0`) su un nodo
   con GPU, PVC per i modelli, Service interno su 11434, nessun egress.
4. **Modelli**: PVC dei modelli dell'API e download iniziale (vedi "Rete").
5. **Segreti**: `ExternalSecret` da Vault (`ClusterSecretStore vault-backend`)
   per `ANONPIP_VAULT_KEY` e, se serve, `ANONPIP_VLM_API_KEY`.
6. **Manifest applicativo**: ConfigMap, Deployment, Service, Ingress e
   NetworkPolicy per API e interfaccia web (modello in `docs/k8s-example/service.yaml`).
7. **Flux**: Kustomization in `clusters/kustomizations/<app>.yaml`;
   `ImageRepository` e `ImagePolicy` in `clusters/image-policy/` per le due immagini:
   - staging: policy `<servizio>-test`, tag `YYYY.MM.DD-test.<epoch>`, ordine numerico su `<epoch>`;
   - prod: policy `<servizio>`, tag `YYYY.MM.DD`.
   La riga `image:` del Deployment porta il marker
   `# {"$imagepolicy": "flux-system:<policy>"}`: Flux aggiorna il tag da solo.
8. **Sonar**: creazione del progetto e chiave da mettere in `sonar-project.properties`.

## Flusso di rilascio

1. MR da feature branch verso `test`: la CI esegue lint, tipi, unit test,
   test del frontend e Sonar.
2. Merge su `test`: la CI costruisce e pubblica `YYYY.MM.DD-test.<epoch>`
   (API e interfaccia web), Flux lo rilascia in **staging**.
3. Validazione in staging.
4. MR da `test` a `main`: la CI pubblica `YYYY.MM.DD`, Flux lo rilascia in **produzione**.

## Regole che non si rompono dopo il primo deploy

- Non rinominare immagini, porta, path delle probe o prefisso delle variabili.
- Ogni nuova variabile ha un default sicuro: un pod non deve andare in crash
  perché la ConfigMap non è ancora aggiornata. Un file di configurazione
  della pipeline non valido (`ANONPIP_PIPELINE_CONFIG_FILE`) invece blocca
  l'avvio: è un errore di deploy, meglio scoprirlo subito.
- Il formato della mappa cifrata (`.mappa.enc`) è un contratto: le mappe già
  consegnate devono restare ripristinabili.
