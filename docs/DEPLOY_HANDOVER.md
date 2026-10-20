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
| Readiness probe | `GET /readyz` (503 finché il server OCR non risponde e i modelli NER non sono caricati: 1-3 minuti all'avvio; ritentato ogni 30 s) |
| Serve un database? | **no**: servizio stateless, i job vivono in memoria e i documenti in una cartella privata fino alla scadenza (`ANONPIP_JOB_TTL_MINUTES`) |
| Esposizione | solo interna (`traefik-int`) / tramite WSO2 API Manager |
| Chi lo chiama | |
| Chi chiama (dipendenze) | il servizio **`paddle-ocr`** (sotto, stesso namespace) su `ANONPIP_OCR_SERVER_URL`: legge le pagine scansionate. Se non risponde, `/readyz` è 503 e nessun documento viene elaborato senza OCR. NER (GLiNER e GLiNER2) gira nel pod |
| Repliche | **1**, oppure 2+ con session affinity sull'ingress: i job sono in memoria del pod che li ha creati |
| Risorse | con GPU: 1 GPU NVIDIA (i due modelli NER occupano circa 4 GB), requests 1 CPU / 4Gi, limits 4 CPU / 8Gi. Senza GPU: requests 2 CPU / 4Gi, limits 8 CPU / 8Gi (più lento). L'OCR è nel servizio `paddle-ocr` |
| Pesi dei modelli | volume montato su `/models` (`HF_HOME=/models/huggingface`), popolato una volta con `just models`; in alternativa un'immagine derivata che li contiene. Il cluster non scarica da internet |
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
| `ANONPIP_OCR_SERVER_URL` | vuoto (URL del servizio `paddle-ocr`, es. `http://paddle-ocr:8829`; vuoto o non raggiungibile = `/readyz` 503) | `http://paddle-ocr:8829` | `http://paddle-ocr:8829` | no |
| `ANONPIP_OCR_DPI` | `300` | | | no |
| `ANONPIP_NER_ENABLED` | `true` | | | no |
| `ANONPIP_NER_MODEL` | `urchade/gliner_multi_pii-v1` | | | no |
| `ANONPIP_NER2_ENABLED` | `true` (secondo modello NER, i suoi risultati si aggiungono; `false` = solo GLiNER: su GPU da 8 GB le scansioni tornano da circa 12 a 4 s per pagina, con circa 1 punto di recall in meno) | | | no |
| `ANONPIP_NER2_MODEL` | `fastino/gliner2-privacy-filter-PII-multi` | | | no |
| `ANONPIP_NER_THRESHOLD` / `ANONPIP_NER_PERSON_THRESHOLD` | `0.5` / `0.4` | | | no |
| `ANONPIP_MAX_UPLOAD_MB` | `30` | | | no |
| `ANONPIP_MAX_PAGES` | `50` | | | no |
| `ANONPIP_JOB_TTL_MINUTES` | `30` | | | no |
| `ANONPIP_MAX_JOBS` | `20` | | | no |
| `ANONPIP_WORK_DIR` | vuoto (cartella temporanea) | path dell'`emptyDir` | path dell'`emptyDir` | no |
| `ANONPIP_REDACT_CLINICAL_DATES` / `ANONPIP_REDACT_AGE` / `ANONPIP_REDACT_ORGANIZATIONS` | `false` | | | no |
| `ANONPIP_REDACTION_STYLE` | `black` | | | no |
| `ANONPIP_VERIFY_PIXEL_TOLERANCE` | `0.0002` | | | no |
| `HF_HOME` | `/models/huggingface` (nell'immagine) | | | no |

Il servizio non ha credenziali: nessun segreto da Vault.

## Secondo servizio: `paddle-ocr`

Server OCR (PaddleOCR PP-OCRv5 dietro l'API OCR di LiteParse), immagine costruita
da `ocr-server/Dockerfile`. Lo chiama solo questo servizio: nessuna esposizione.

| Voce | Valore |
|---|---|
| Immagine | `harbor.agenas.oci/agenas/paddle-ocr-server` (build arg `PADDLE_EXTRA=gpu` su nodo GPU, `cpu` altrimenti) |
| Porta | `8829` (nome `ocr`), Service `paddle-ocr` nello stesso namespace |
| Liveness / readiness | `GET /health` (503 finché i modelli non sono caricati) |
| Risorse | con GPU: 1 GPU NVIDIA (circa 3 GB), requests 1 CPU / 3Gi, limits 2 CPU / 6Gi. Su CPU funziona, più lento |
| Modelli | volume su `/models`, sottocartelle `/models/paddleocr/PP-OCRv5_server_det` e `/models/paddleocr/latin_PP-OCRv5_mobile_rec` (`OCRSRV_MODEL_DIR=/models/paddleocr`, già nell'immagine); vanno copiate dalla cache PaddleX dopo un primo avvio con internet |
| Variabili | `OCRSRV_DET_LIMIT_SIDE_LEN` (`2400`), `OCRSRV_MARGIN_PT` (`60`), `OCRSRV_MARGIN_MIN_SCORE` (`0.5`), `OCRSRV_MAX_UPLOAD_MB` (`40`), `OCRSRV_DEVICE` (`auto`), `OCRSRV_LOG_LEVEL` (`INFO`); nessun segreto |
| Dati | riceve immagini di pagine (dati sanitari): non scrive su disco e non registra il testo letto |

## Cosa fa il team piattaforma

1. **Namespace**: cartella `<namespace>/` in `oke-staging` e `oke-prod` con `ns.yaml`.
2. **Database**: non serve.
3. **Modelli e GPU**: un nodo con GPU NVIDIA (o nodi CPU, più lenti) e un volume
   con i pesi di GLiNER e GLiNER2 montato su `/models`, popolato con `just models`.
   Il servizio `paddle-ocr` ha il suo volume di modelli (sezione sotto).
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
