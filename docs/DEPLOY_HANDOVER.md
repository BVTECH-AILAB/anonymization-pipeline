# Deploy handover

Chi sviluppa il servizio **non modifica** i repository Flux (`oke-staging`,
`oke-prod`). Compila questa scheda e la consegna al team piattaforma, che
fa il provisioning.

## Cosa consegna lo sviluppatore

| Voce | Valore |
|---|---|
| Nome servizio / immagine | `anonmyzation-pipeline` -> `harbor.agenas.oci/agenas/anonmyzation-pipeline` |
| Repository GitLab | `gitlab.agenas.oci:application/backend/anonmyzation-pipeline` |
| Build | `docker build -f apps/api/Dockerfile --target runtime .` (contesto: la root del repository, che contiene anche il motore `packages/anonymization-core`) |
| Namespace proposto | |
| Porta | `8000` (nome `web`) |
| Liveness probe | `GET /healthz` |
| Readiness probe | `GET /readyz` (503 finché il server OCR non risponde e il servizio NER non risponde a `/health`; il controllo viene ritentato ogni 30 s) |
| Serve un database? | **no**: servizio stateless, i job vivono in memoria e i documenti in una cartella privata fino alla scadenza (`ANONPIP_JOB_TTL_MINUTES`) |
| Esposizione | solo interna (`traefik-int`) / tramite WSO2 API Manager |
| Chi lo chiama | |
| Chi chiama (dipendenze) | il servizio **`paddle-ocr`** (sotto, stesso namespace) su `ANONPIP_OCR_SERVER_URL`: legge le pagine scansionate. Se non risponde, `/readyz` è 503 e nessun documento viene elaborato senza OCR. Il **servizio NER AILAB** (`POST /batch_ner`, `GET /health`): riceve il testo estratto dalle pagine, quindi va raggiunto all'interno del cluster. In staging risponde su `ner.agenas-stg.oci`, ma il suo certificato HTTPS non è verificabile (il server non presenta la CA): serve l'URL interno del Service oppure la CA in `ANONPIP_NER_CA_BUNDLE`. Se non risponde, i job falliscono senza rilasciare nulla (fail-closed) |
| Repliche | **1**, oppure 2+ con session affinity sull'ingress: i job sono in memoria del pod che li ha creati |
| Risorse | nessuna GPU nel pod (OCR nel servizio `paddle-ocr`, NER nel servizio AILAB): requests 1 CPU / 4Gi, limits 4 CPU / 8Gi, da rimisurare |
| Pesi dei modelli | nessuno nel pod: i modelli OCR stanno nel volume del servizio `paddle-ocr` (sezione sotto) |
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
| `ANONPIP_PRELOAD_MODELS` | `true` | `true` | `true` | no |
| `ANONPIP_OCR_SERVER_URL` | vuoto (URL del servizio `paddle-ocr`, es. `http://paddle-ocr:8829`; vuoto o non raggiungibile = `/readyz` 503) | `http://paddle-ocr:8829` | `http://paddle-ocr:8829` | no |
| `ANONPIP_OCR_DPI` | `300` | | | no |
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

## Secondo servizio: `paddle-ocr`

Server OCR (modelli PP-OCRv5 su RapidOCR e ONNX Runtime, dietro l'API OCR di LiteParse `POST /ocr`, più
`POST /recognize` per rileggere una sola riga), immagine costruita da `ocr-server/Dockerfile`. Lo chiama solo
questo servizio: nessuna esposizione.

| Voce | Valore |
|---|---|
| Immagine | `harbor.agenas.oci/agenas/paddle-ocr-server` (build arg `OCR_EXTRA=gpu` su nodo GPU, `cpu` altrimenti) |
| Porta | `8829` (nome `ocr`), Service `paddle-ocr` nello stesso namespace |
| Liveness / readiness | `GET /health` (503 finché i modelli non sono caricati) |
| Risorse | con GPU: 1 GPU NVIDIA (picco circa 4 GB per una pagina a 300 dpi, 0,3 GB a riposo: la memoria viene rilasciata dopo ogni lettura; tetto opzionale `OCRSRV_GPU_MEM_LIMIT_MB`, almeno 4500 se usato), requests 1 CPU / 3Gi, limits 2 CPU / 6Gi. Il servizio non usa la GPU (il NER gira nel servizio AILAB) |
| Modelli | volume su `/models`, cartella `/models/rapidocr` (`OCRSRV_MODEL_DIR`, già nell'immagine) con `ch_PP-OCRv5_det_server.onnx`, `latin_PP-OCRv5_rec_mobile.onnx` e `ch_ppocr_mobile_v2.0_cls_mobile.onnx` (circa 90 MB). il server li carica all'avvio leggendo un'immagine di prova e, se mancano, li scarica: popolare il volume con un primo avvio con internet, poi copiarlo. Senza modelli e senza internet il container termina all'avvio |
| Variabili | `OCRSRV_MARGIN_PT` (`60`), `OCRSRV_MARGIN_MIN_SCORE` (`0.5`), `OCRSRV_MAX_UPLOAD_MB` (`40`), `OCRSRV_DEVICE` (`auto`: GPU se utilizzabile, altrimenti CPU con un WARNING all'avvio; `gpu`: senza GPU il container termina; `cpu`), `OCRSRV_GPU_MEM_LIMIT_MB` (`0` = nessun tetto, in MiB), `OCRSRV_LOG_LEVEL` (`INFO`); nessun segreto |
| Dati | riceve immagini di pagine (dati sanitari): non scrive su disco e non registra il testo letto |

## Cosa fa il team piattaforma

1. **Namespace**: cartella `<namespace>/` in `oke-staging` e `oke-prod` con `ns.yaml`.
2. **Database**: non serve.
3. **Modelli e GPU**: nessuno per questo servizio. La GPU (opzionale) e il volume
   dei modelli servono solo al servizio `paddle-ocr` (sezione sotto).
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
