# Deploy handover

Chi sviluppa il servizio **non modifica** i repository Flux (`oke-staging`,
`oke-prod`). Compila questa scheda e la consegna al team piattaforma, che
fa il provisioning.

## Cosa consegna lo sviluppatore

| Voce | Valore |
|---|---|
| Nome servizio / immagine | `anonymization-pipeline` -> `harbor.agenas.oci/agenas/anonymization-pipeline` |
| Repository GitLab | `gitlab.agenas.oci:application/backend/anonymization-pipeline` |
| Namespace proposto | |
| Porta | `8000` (nome `web`) |
| Liveness probe | `GET /healthz` |
| Readiness probe | `GET /readyz` |
| Serve un database? | no (servizio senza stato) |
| Esposizione | solo interna (`traefik-int`) / tramite WSO2 API Manager |
| Chi lo chiama | |
| Chi chiama (dipendenze) | es. llm-gateway, ner-anonymizer, authz |
| Repliche | 1 (i lavori vivono in memoria: con più repliche serve affinità di sessione) |
| Risorse | da misurare con i modelli caricati (GLiNER su CPU: circa 1,5 GB di RAM; 4 thread torch) |
| Modelli | volume `/models` (`HF_HOME=/models/hf`, `DOCTR_CACHE_DIR=/models/doctr`), popolato una volta con `python -m anonymization_pipeline.model_store` (unico passo con rete); a runtime `HF_HUB_OFFLINE=1` |
| Readiness con i modelli | `/readyz` risponde 503 finché i modelli configurati (NER, OCR) non sono caricati; se mancano resta 503 e i documenti vengono rifiutati |

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
| `ANONPIP_PIPELINE_CONFIG_FILE` | vuoto (default del pacchetto) | | | no |
| `ANONPIP_MAX_UPLOAD_MB` | `30` | | | no |
| `ANONPIP_MAX_QUEUED_JOBS` | `20` | | | no |
| `ANONPIP_MAX_KEPT_JOBS` | `50` | | | no |
| `ANONPIP_JOB_TTL_S` | `1800` | | | no |
| `ANONPIP_VAULT_KEY` | nessuno (credenziale) | Vault, solo se si usa `mode=pseudonym` | Vault, solo se si usa `mode=pseudonym` | **sì** |

`ANONPIP_PIPELINE_CONFIG_FILE` punta a un YAML montato da ConfigMap con le
sole chiavi da cambiare di `resources/default.yaml`. Un file non valido
(chiave sconosciuta, categoria protetta disattivata) blocca l'avvio del pod.

## Cosa fa il team piattaforma

1. **Namespace**: cartella `<namespace>/` in `oke-staging` e `oke-prod` con `ns.yaml`.
2. **Database** (se serve): cluster CloudNativePG `postgres-<servizio>`
   (Postgres 17, 2 istanze, backup barman su S3 e `ScheduledBackup`).
   CNPG genera il secret `postgres-<servizio>-app`; la chiave `uri` diventa
   `ANONPIP_DATABASE_URL`. Il servizio non crea database né utenti.
3. **Segreti**: `ExternalSecret` da Vault (`ClusterSecretStore vault-backend`).
4. **Manifest applicativo**: ConfigMap, Deployment, Service, Ingress
   (modello in `docs/k8s-example/service.yaml`).
5. **Flux**: Kustomization in `clusters/kustomizations/<app>.yaml`;
   `ImageRepository` e `ImagePolicy` in `clusters/image-policy/`:
   - staging: policy `<servizio>-test`, tag `YYYY.MM.DD-test.<epoch>`, ordine numerico su `<epoch>`;
   - prod: policy `<servizio>`, tag `YYYY.MM.DD`.
   La riga `image:` del Deployment porta il marker
   `# {"$imagepolicy": "flux-system:<policy>"}`: Flux aggiorna il tag da solo.
6. **Sonar**: creazione del progetto e chiave da mettere in `sonar-project.properties`.

## Flusso di rilascio

1. MR da feature branch verso `test`: la CI esegue lint, tipi, unit test,
   integration test e Sonar.
2. Merge su `test`: la CI costruisce e pubblica `YYYY.MM.DD-test.<epoch>`,
   Flux lo rilascia in **staging**.
3. Validazione in staging.
4. MR da `test` a `main`: la CI pubblica `YYYY.MM.DD`, Flux lo rilascia in **produzione**.

## Regole che non si rompono dopo il primo deploy

- Non rinominare immagine, porta, path delle probe o prefisso delle variabili.
- Ogni nuova variabile ha un default sicuro: un pod non deve andare in crash
  perché la ConfigMap non è ancora aggiornata.
- Le migrazioni devono essere compatibili con l'immagine precedente
  (prima si aggiunge, in un rilascio successivo si rimuove): durante il
  rolling update convivono pod vecchi e nuovi.
