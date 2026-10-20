# Deploy handover

Chi sviluppa il servizio **non modifica** i repository Flux (`oke-staging`,
`oke-prod`). Compila questa scheda e la consegna al team piattaforma, che
fa il provisioning.

## Cosa consegna lo sviluppatore

| Voce | Valore |
|---|---|
| Nome servizio / immagine | `anonmyzation-pipeline` -> `harbor.agenas.oci/agenas/anonmyzation-pipeline` |
| Repository GitLab | `gitlab.agenas.oci:application/backend/anonmyzation-pipeline` |
| Namespace proposto | |
| Porta | `8000` (nome `web`) |
| Liveness probe | `GET /healthz` |
| Readiness probe | `GET /readyz` (503 finché i modelli OCR non sono caricati e il servizio NER non risponde a `/health`: 1-3 minuti all'avvio; il controllo viene ritentato ogni 30 s) |
| Serve un database? | **no**: servizio stateless, i job vivono in memoria e i documenti in una cartella privata fino alla scadenza (`ANONPIP_JOB_TTL_MINUTES`) |
| Esposizione | solo interna (`traefik-int`) / tramite WSO2 API Manager |
| Chi lo chiama | |
| Chi chiama (dipendenze) | **servizio NER AILAB** (`POST /batch_ner`, `GET /health`): riceve il testo estratto dalle pagine, quindi va raggiunto all'interno del cluster. In staging risponde su `ner.agenas-stg.oci`, ma il suo certificato HTTPS non è verificabile (il server non presenta la CA): serve l'URL interno del Service oppure la CA in `ANONPIP_NER_CA_BUNDLE`. Se non risponde, i job falliscono senza rilasciare nulla (fail-closed). L'OCR (PaddleOCR PP-OCRv5 via RapidOCR: rilevamento su GPU con PyTorch, riconoscimento con ONNX Runtime) gira nel pod |
| Repliche | **1**, oppure 2+ con session affinity sull'ingress: i job sono in memoria del pod che li ha creati |
| Risorse | con GPU: 1 GPU NVIDIA (l'OCR occupa circa 3 GB per worker, vedi `ANONPIP_OCR_WORKERS`; il NER non usa più la GPU del pod, dimensionamento da rimisurare), requests 1 CPU / 4Gi, limits 4 CPU / 8Gi. Senza GPU: requests 2 CPU / 4Gi, limits 8 CPU / 8Gi (circa 5-10 volte più lento).|
| Pesi dei modelli | volume montato su `/models` (`ANONPIP_OCR_MODEL_DIR=/models/rapidocr`), popolato una volta con `just models`; in alternativa un'immagine derivata che li contiene. Il cluster non scarica da internet |
| Spazio temporaneo | `emptyDir` per `ANONPIP_WORK_DIR` (o la `/tmp` del container): i documenti restano solo per `ANONPIP_JOB_TTL_MINUTES` |

### Variabili d'ambiente

Una riga per ogni campo di `Settings`. Segreto = arriva da Vault, non va
nella ConfigMap e nel codice non ha default.

| Variabile | Default | Staging | Prod | Segreto |
|---|---|---|---|---|
| `ANONPIP_ENVIRONMENT` | `prod` | `staging` | `prod` | no |
| `ANONPIP_LOG_LEVEL` | `INFO` | `DEBUG` | `INFO` | no |
| `ANONPIP_EXTENDED_LOG_CONTEXT` | `false` | `false` (solo per debug mirato) | ignorata | no |
| `ANONPIP_LOG_MASK_PREFIX` / `ANONPIP_LOG_MASK_SUFFIX` | `1` / `1` | | | no |
| `ANONPIP_UI_ENABLED` | `false` | `true` (pagina demo) | `false` | no |
| `ANONPIP_DEVICE` | `auto` | `auto` | `auto` | no |
| `ANONPIP_PRELOAD_MODELS` | `true` | `true` | `true` | no |
| `ANONPIP_OCR_MODEL_TYPE` | `server` | | | no |
| `ANONPIP_OCR_MODEL_DIR` | vuoto (nel pacchetto); nell'immagine `/models/rapidocr` | | | no |
| `ANONPIP_OCR_DPI` | `300` | | | no |
| `ANONPIP_OCR_WORKERS` | `1` (pagine lette in parallelo, un motore OCR ciascuna; ognuno occupa circa 3 GB di GPU: aumentarlo solo su GPU grandi, es. L40S, dopo una misura) | | | no |
| `ANONPIP_OCR_MARGIN_PT` | `60` | | | no |
| `ANONPIP_NER_ENABLED` | `true` | | | no |
| `ANONPIP_NER_URL` | vuoto (`/readyz` resta 503) | URL interno del servizio NER AILAB di staging | URL interno del servizio NER AILAB di produzione | no |
| `ANONPIP_NER_TIMEOUT_SECONDS` | `30` | | | no |
| `ANONPIP_NER_RETRIES` | `2` (nuovi tentativi su timeout, errore di connessione, 502/503/504; poi il job fallisce) | | | no |
| `ANONPIP_NER_RETRY_BACKOFF_SECONDS` | `1` (prima attesa, raddoppia a ogni tentativo: 1 s, 2 s) | | | no |
| `ANONPIP_NER_CA_BUNDLE` | vuoto (CA di sistema) | path del PEM della CA interna, se il NER è in HTTPS con certificato interno | idem | no |
| `ANONPIP_NER_THRESHOLD` / `ANONPIP_NER_PERSON_THRESHOLD` | `0.5` / `0.4` | | | no |
| `ANONPIP_MAX_UPLOAD_MB` | `30` | | | no |
| `ANONPIP_MAX_PAGES` | `50` | | | no |
| `ANONPIP_JOB_TTL_MINUTES` | `30` | | | no |
| `ANONPIP_MAX_JOBS` | `20` | | | no |
| `ANONPIP_WORK_DIR` | vuoto (cartella temporanea) | path dell'`emptyDir` | path dell'`emptyDir` | no |
| `ANONPIP_REDACT_CLINICAL_DATES` / `ANONPIP_REDACT_AGE` / `ANONPIP_REDACT_ORGANIZATIONS` | `false` | | | no |
| `ANONPIP_REDACTION_STYLE` | `black` | | | no |
| `ANONPIP_VERIFY_PIXEL_TOLERANCE` | `0.0002` | | | no |

Il servizio non ha credenziali: nessun segreto da Vault.

## Cosa fa il team piattaforma

1. **Namespace**: cartella `<namespace>/` in `oke-staging` e `oke-prod` con `ns.yaml`.
2. **Database**: non serve.
3. **Modelli e GPU**: un nodo con GPU NVIDIA (o nodi CPU, più lenti) e un volume
   con i modelli di PaddleOCR montato su `/models`. Su un
   nodo GPU il volume va popolato con `ANONPIP_DEVICE=cuda just models`, che
   scarica anche il rilevatore di testo per PyTorch usato sulla GPU.
4. **Segreti**: nessuno.
5. **Manifest applicativo**: ConfigMap, Deployment, Service, Ingress
   (modello in `docs/k8s-example/service.yaml`).
6. **Flux**: Kustomization in `clusters/kustomizations/<app>.yaml`;
   `ImageRepository` e `ImagePolicy` in `clusters/image-policy/`:
   - staging: policy `<servizio>-test`, tag `YYYY.MM.DD-test.<epoch>`, ordine numerico su `<epoch>`;
   - prod: policy `<servizio>`, tag `YYYY.MM.DD`.
   La riga `image:` del Deployment porta il marker
   `# {"$imagepolicy": "flux-system:<policy>"}`: Flux aggiorna il tag da solo.
7. **Sonar**: creazione del progetto e chiave da mettere in `sonar-project.properties`.

## Flusso di rilascio

1. MR da feature branch verso `test`: la CI esegue lint, tipi, unit test e Sonar.
2. Merge su `test`: la CI costruisce e pubblica `YYYY.MM.DD-test.<epoch>`,
   Flux lo rilascia in **staging**.
3. Validazione in staging.
4. MR da `test` a `main`: la CI pubblica `YYYY.MM.DD`, Flux lo rilascia in **produzione**.

## Regole che non si rompono dopo il primo deploy

- Non rinominare immagine, porta, path delle probe o prefisso delle variabili.
- Ogni nuova variabile ha un default sicuro: un pod non deve andare in crash
  perché la ConfigMap non è ancora aggiornata.
- Durante il rolling update i job in corso sul pod che si ferma vanno persi:
  il medico ricarica il documento (nessun dato persiste oltre la scadenza).
