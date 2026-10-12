# Piano lavori - anonymization-pipeline

Stato avanzamento lavori del servizio di anonimizzazione dei documenti medici
(piattaforma MIA, AGENAS). Il piano copre anche le attività già concluse, così
il Gantt mostra il percorso completo dallo studio preliminare al go-live.

- Sorgente per il Gantt: [`piano_lavori.csv`](piano_lavori.csv) (stesse righe e
  stessi ID delle tabelle qui sotto).
- Punti aperti collegati ai task: [`PUNTI_APERTI.md`](PUNTI_APERTI.md).
- Stato rilevato il 12/10/2026 sul branch `feat/integrate-anonimizzatore-vlm`
  (ultimo commit `d08da2a`).

## Legenda

| Campo | Significato |
|---|---|
| ID | Numero progressivo, usato nella colonna Predecessori del CSV |
| WBS | Codice gerarchico: `N` fase, `N.M` attività o milestone |
| Livello | 1 fase (riepilogo), 2 attività o milestone |
| Tipo | `Fase`, `Task`, `Milestone` (durata 0) |
| Durata (gg) | Giorni-uomo stimati. Per le fasi è la somma delle attività |
| Predecessori | Dipendenze fine-inizio. Nel CSV sono ID separati da `;`, qui codici WBS |
| Stato | `Completato`, `In corso`, `Da fare`, `Da decidere` |
| % | Avanzamento. Per le fasi è la media pesata sulla durata |
| Punti aperti | Codici `PA-xx` di [`PUNTI_APERTI.md`](PUNTI_APERTI.md) che condizionano il task |
| Riferimento | File o commit in cui il lavoro è visibile |

`Da decidere` indica un'attività che esiste solo se il punto aperto collegato
la rende necessaria (per esempio l'ingest DOCX o la persistenza dei job). Resta
nel piano con la sua stima finché il punto non viene chiuso: se la decisione è
negativa, la riga si elimina e si ricalcolano le date.

## Criteri di stima

- Le durate dei task completati sono stime a posteriori dell'impegno, non il
  tempo di calendario impiegato: servono a pesare l'avanzamento delle fasi.
- Le durate dei task da fare presuppongono una persona a tempo pieno, senza
  tempi di attesa esterni (approvazioni, provisioning del platform team, DPO).
  Questi vanno aggiunti nel Gantt come ritardi sulle milestone.
- Le date non sono nel file: il tool Gantt le calcola dalla data di inizio e
  dalle dipendenze.

## Riepilogo per fase

| Fase | Giorni stimati | Avanzamento | Stato |
|---|---:|---:|---|
| 1. Studio preliminare | 23 | 44% | In corso |
| 2. Best practice di sviluppo | 9 | 78% | In corso |
| 3. Dataset | 16 | 9% | In corso |
| 4. Sviluppo | 64 | 70% | In corso |
| 5. Definizione metriche | 11 | 36% | In corso |
| 6. Compliance e linee guida AgID | 14 | 6% | In corso |
| 7. Testbook | 8 | 25% | In corso |
| 8. Test, verifica metriche, integrazione | 21 | 6% | In corso |
| **Totale** | **166** | **43%** | |

### 1. Studio preliminare

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 2 | 1.1 | Raccolta requisiti funzionali: formati di input/output, modalità di anonimizzazione, categorie di dati da censurare | 3 |  | In corso | 60 | PA-05, PA-13, PA-14 | `README.md; resources/categories.yaml` |
| 3 | 1.2 | Studio delle tecniche di estrazione (docling per PDF nativi, VLM-OCR per scansioni e immagini, fallback) | 3 |  | Completato | 100 |  | `extraction/; 6880092` |
| 4 | 1.3 | Studio delle tecniche di rilevamento (regole con checksum, etichette, NER, VLM, filtro eponimi) | 4 |  | Completato | 100 |  | `detection/, detectors/; 2705d5d` |
| 5 | 1.4 | Vincoli prestazionali: latenza massima per pagina e per documento, budget di chiamate VLM, dimensionamento GPU | 2 | 1.1 | Da fare | 0 | PA-01, PA-18 |  |
| 6 | 1.5 | Requisiti su residui e quarantena dei documenti rifiutati | 2 | 1.1 | In corso | 40 | PA-09 | `verification/ (residui: off/report/block)` |
| 7 | 1.6 | Requisiti di reversibilità: custodia di mappa e chiave, richiesta del documento originale | 2 | 1.1 | In corso | 30 | PA-01, PA-06 | `redaction/vault.py` |
| 8 | 1.7 | Analisi dell'integrazione nel flusso MIA: chiamanti, modulo NER esistente, autenticazione, deroghe al template | 3 | 1.1 | Da fare | 0 | PA-02, PA-03, PA-10, PA-11 | `docs/DEPLOY_HANDOVER.md` |
| 9 | 1.8 | Valutazione JEV | 2 | 1.1 | Da fare | 0 | PA-07 |  |
| 10 | 1.9 | Chiusura dei punti aperti e registro delle decisioni | 2 | 1.4, 1.5, 1.6, 1.7, 1.8 | Da fare | 0 | tutti | `docs/PUNTI_APERTI.md` |
| 11 | 1.10 | **M1 Requisiti approvati** | 0 | 1.9 | Da fare | 0 |  |  |

### 2. Best practice di sviluppo

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 13 | 2.1 | Setup del repository dal template (uv, ruff, ty, just, CI GitLab, Sonar, immagini) | 2 |  | Completato | 100 |  | `d445bd6; 1e6fd6d; ef04781` |
| 14 | 2.2 | Convenzioni di configurazione: file YAML delle risorse, schema validato, catalogo modelli, nessun nome di modello nel codice | 2 | 2.1 | Completato | 100 |  | `pipeline_config/; resources/; 8210050; 4d950e1` |
| 15 | 2.3 | Regole di logging e privacy, eventi KPI per la dashboard | 1 | 2.1 | Completato | 100 |  | `docs/LOGGING_AND_PRIVACY.md; ab5d9dc` |
| 16 | 2.4 | Strategia di test: FakeVLM, test di non-leak, marker integration/ner/docling, copertura 80% | 2 | 2.1 | Completato | 100 |  | `tests/pipeline.py; tests/conftest.py` |
| 17 | 2.5 | CLAUDE.md di progetto (riscrittura del template MIA) | 1 | 2.2, 2.3, 2.4 | Da fare | 0 |  | `CLAUDE.md` |
| 18 | 2.6 | Pulizia dei residui del template: doc ai-platform, chiave Sonar CHANGEME, docstring superate, helper inutilizzati in privacy.py | 1 | 2.1 | Da fare | 0 | PA-19 | `sonar-project.properties; pipeline.py; privacy.py` |

### 3. Dataset

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 20 | 3.1 | Schema di annotazione (span e categoria da categories.yaml) e linee guida per gli annotatori | 2 | 1.1 | Da fare | 0 | PA-14 | `resources/categories.yaml` |
| 21 | 3.2 | Documenti sintetici per tipologia: referti, ricette, lettere di dimissione, documenti d'identità, testo libero | 5 | 3.1 | In corso | 30 |  | `tests/fixtures/ (4 documenti)` |
| 22 | 3.3 | Varianti di degrado: scansioni storte, bassa risoluzione, timbri, firme, tabelle, manoscritto | 3 | 3.2 | Da fare | 0 | PA-16 |  |
| 23 | 3.4 | Annotazione della ground truth (riuso dell'export .revisione.json del frontend) | 5 | 3.2, 3.3 | Da fare | 0 |  | `frontend/src/lib/revisione.ts` |
| 24 | 3.5 | Split dev/test, versioning del dataset, verifica dell'assenza di dati reali | 1 | 3.4 | Da fare | 0 |  |  |
| 25 | 3.6 | **M3 Dataset pronto** | 0 | 3.5 | Da fare | 0 |  |  |

### 4. Sviluppo

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 27 | 4.1 | Prototipi di confronto tra VLM e librerie (latenza, accuratezza) | 5 | 1.2, 1.3 | In corso | 40 | PA-04, PA-08 | `resources/models.yaml; d08da2a` |
| 28 | 4.2 | Ingest di PDF, immagini e testo con limiti di pagine e pixel | 3 | 2.1 | Completato | 100 |  | `ingest/; 6880092` |
| 29 | 4.3 | Ingest di DOCX e altri formati Office | 2 | 1.10 | Da decidere | 0 | PA-05 | `ingest/ (oggi rifiutati)` |
| 30 | 4.4 | Estrazione: docling per le pagine native, VLM per le altre, fallback | 4 | 4.2 | Completato | 100 |  | `extraction/; 6880092` |
| 31 | 4.5 | Detector a regole con validazione dei checksum (CF, P.IVA, IBAN, carte) | 3 | 4.4 | Completato | 100 |  | `detectors/rules/; 2705d5d` |
| 32 | 4.6 | Detector a etichette (etichetta -> valore, tabelle normalizzate) | 2 | 4.4 | Completato | 100 |  | `detectors/labels/; 2705d5d` |
| 33 | 4.7 | Detector NER locale (Hugging Face, CPU) | 2 | 4.4 | Completato | 100 |  | `detectors/ner/; 2705d5d` |
| 34 | 4.8 | Integrazione del modulo NER esistente (altra repo) | 3 | 1.10, 4.7 | Da decidere | 0 | PA-02 |  |
| 35 | 4.9 | Detection VLM multi-passata e ancoraggio anti-allucinazione | 4 | 4.4 | Completato | 100 |  | `detection/; 2705d5d` |
| 36 | 4.10 | Filtro contestuale di eponimi, scale cliniche e farmaci | 3 | 4.5, 4.6, 4.7, 4.9 | Completato | 100 |  | `detectors/eponyms.py; 9453700; 56d4cc8` |
| 37 | 4.11 | Redaction: coreferenza VLM e modalità pseudonym/tag/redaction | 4 | 4.10 | Completato | 100 |  | `redaction/; c0663a6` |
| 38 | 4.12 | Verification off/report/block e controllo invariante | 3 | 4.11 | Completato | 100 |  | `verification/; cb55e36; 2b097b2` |
| 39 | 4.13 | Mappa cifrata AES-256-GCM e ripristino | 3 | 4.11 | Completato | 100 |  | `redaction/vault.py; c0663a6` |
| 40 | 4.14 | API sincrona e job asincroni in memoria | 3 | 4.12, 4.13 | Completato | 100 |  | `api/; services/; b5b687e` |
| 41 | 4.15 | Persistenza dei job e archivio dei documenti anonimizzati | 4 | 1.10, 4.14 | Da decidere | 0 | PA-01, PA-06, PA-12 |  |
| 42 | 4.16 | Quarantena dei documenti rifiutati | 3 | 1.10, 4.14 | Da decidere | 0 | PA-09 |  |
| 43 | 4.17 | Autenticazione e autorizzazione dei chiamanti e del ripristino | 3 | 1.10, 4.14 | Da decidere | 0 | PA-03, PA-06 |  |
| 44 | 4.18 | Limite globale di concorrenza VLM (oggi vale per singolo documento) | 1 | 4.14 | Da fare | 0 | PA-12 | `vlm/client.py` |
| 45 | 4.19 | Frontend web: carica, confronto, dashboard, storico, ripristino, stato | 6 | 4.14 | Completato | 100 |  | `frontend/; 57e39a2` |
| 46 | 4.20 | Download offline dei modelli, container, compose con GPU, reti isolate | 3 | 4.14 | Completato | 100 |  | `model_store.py; docker-compose.yml; 6b2a463` |
| 47 | 4.21 | **M2 Modelli scelti** | 0 | 4.1, 5.4, 3.6 | Da fare | 0 | PA-04, PA-08 |  |
| 48 | 4.22 | **M4 Feature complete** | 0 | 4.3, 4.8, 4.15, 4.16, 4.17, 4.18, 4.19, 4.20, 4.21 | Da fare | 0 |  |  |

### 5. Definizione metriche

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 50 | 5.1 | Metriche prestazionali: latenza p50/p95, secondi per pagina, token, CPU e memoria | 2 | 2.1 | Completato | 100 |  | `metrics.py; 99e0df7` |
| 51 | 5.2 | Metriche di qualità: recall per categoria, precision, F1, tasso di residui, allucinazioni, over-redaction | 2 | 1.10 | Da fare | 0 | PA-15 |  |
| 52 | 5.3 | Soglie di accettazione per categoria | 1 | 5.2 | Da fare | 0 | PA-14, PA-15 |  |
| 53 | 5.4 | Harness di valutazione automatica sul dataset annotato | 4 | 5.2, 3.1 | Da fare | 0 |  |  |
| 54 | 5.5 | Dashboard di confronto tra configurazioni | 2 | 5.1 | Completato | 100 |  | `frontend/src/pagine/Dashboard.tsx, Storico.tsx` |

### 6. Compliance e linee guida AgID

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 56 | 6.1 | GDPR: anonimizzazione o pseudonimizzazione (Considerando 26, parere WP29 05/2014), minimizzazione, retention | 2 | 1.10 | Da fare | 0 | PA-13 |  |
| 57 | 6.2 | Supporto alla DPIA insieme al DPO | 3 | 6.1 | Da fare | 0 |  |  |
| 58 | 6.3 | Verifica delle linee guida AgID applicabili (sviluppo sicuro, misure minime ICT, IA nella PA) e dei requisiti ACN | 3 | 1.10 | Da fare | 0 |  |  |
| 59 | 6.4 | AI Act: classificazione del sistema e documentazione tecnica | 2 | 1.10 | Da fare | 0 |  |  |
| 60 | 6.5 | Licenze dei modelli e delle librerie | 1 | 4.1 | Da fare | 0 | PA-17 | `resources/models.yaml; pyproject.toml` |
| 61 | 6.6 | Sicurezza: Sonar, pip-audit, CVE delle immagini, NetworkPolicy, gestione della chiave della mappa | 2 | 2.1 | In corso | 40 | PA-01 | `.gitlab-ci.yml; docs/DEPLOY_HANDOVER.md` |
| 62 | 6.7 | Approvazione delle deroghe al template (niente llm-gateway, niente ner-anonymizer, job in memoria) | 1 | 1.7 | Da fare | 0 | PA-11 | `docs/DEPLOY_HANDOVER.md` |

### 7. Testbook

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 64 | 7.1 | Casi di test funzionali per formato e tipologia di documento | 3 | 1.10, 3.2 | Da fare | 0 | PA-05 |  |
| 65 | 7.2 | Casi negativi e di robustezza: file corrotti, limiti superati, VLM non disponibile (fail-closed) | 2 | 1.10 | In corso | 50 |  | `tests/unit/ (test_api, test_pipeline)` |
| 66 | 7.3 | Casi di non-regressione privacy: log, report e metriche senza valori | 1 | 2.3 | Completato | 100 |  | `tests/unit/test_logging.py` |
| 67 | 7.4 | Casi di carico e latenza | 1 | 1.4 | Da fare | 0 | PA-18 |  |
| 68 | 7.5 | Criteri di accettazione e modello di verbale di collaudo | 1 | 5.3, 7.1 | Da fare | 0 |  |  |

### 8. Test, verifica metriche, integrazione

| ID | WBS | Attività | gg | Predecessori | Stato | % | Punti aperti | Riferimento |
|---:|---|---|---:|---|---|---:|---|---|
| 70 | 8.1 | Esecuzione del benchmark sul test set e confronto con le soglie | 3 | 4.22, 3.6, 5.4, 7.5 | Da fare | 0 |  |  |
| 71 | 8.2 | Tuning (prompt, soglie NER, passate, modelli) e iterazione | 5 | 8.1 | Da fare | 0 |  | `resources/prompts/; resources/default.yaml` |
| 72 | 8.3 | Test di carico sulla GPU target | 2 | 4.22, 7.4 | Da fare | 0 | PA-18 |  |
| 73 | 8.4 | Integrazione con i chiamanti | 4 | 4.22 | Da fare | 0 | PA-10 |  |
| 74 | 8.5 | Handover al platform team (namespace, chiamanti, risorse, segreti) | 1 | 1.7, 4.20, 6.7 | In corso | 50 | PA-19 | `docs/DEPLOY_HANDOVER.md` |
| 75 | 8.6 | **M5 Metriche raggiunte** | 0 | 8.2, 8.3 | Da fare | 0 | PA-15 |  |
| 76 | 8.7 | Deploy in staging e collaudo con gli utenti (UAT) | 3 | 8.6, 8.4, 8.5, 6.2, 6.3, 6.4, 6.5, 6.6 | Da fare | 0 |  |  |
| 77 | 8.8 | **M6 Collaudo superato** | 0 | 8.7 | Da fare | 0 |  |  |
| 78 | 8.9 | Documentazione finale (PDF di architettura, manuale d'uso) | 2 | 4.14 | In corso | 40 |  | `docs/architettura-anonymization-pipeline.pdf` |
| 79 | 8.10 | Rilascio in produzione | 1 | 8.8, 8.9 | Da fare | 0 |  |  |
| 80 | 8.11 | **M7 Go-live** | 0 | 8.10 | Da fare | 0 |  |  |

## Milestone e catena principale

| Milestone | Condizione |
|---|---|
| M1 Requisiti approvati | Punti aperti chiusi o con decisione registrata (1.9) |
| M2 Modelli scelti | Prototipi (4.1) valutati con l'harness (5.4) sul dataset pronto (M3) |
| M3 Dataset pronto | Dataset annotato, suddiviso e verificato (3.5) |
| M4 Feature complete | Tutti i task di sviluppo chiusi o eliminati dalle decisioni, modelli scelti |
| M5 Metriche raggiunte | Benchmark e tuning sopra le soglie (8.2), test di carico superato (8.3) |
| M6 Collaudo superato | UAT in staging, compliance completata (6.x) |
| M7 Go-live | Rilascio in produzione |

Cammino critico sul lavoro residuo (determina la data di fine):
1.1 -> 3.1 -> 3.2 -> 3.3 -> 3.4 -> 3.5 -> 3.6 (M3) -> 4.21 (M2) -> 4.22 (M4)
-> 8.1 -> 8.2 -> 8.6 (M5) -> 8.7 -> 8.8 (M6) -> 8.10 -> 8.11 (M7),
circa 28 giorni-uomo residui. Il calcolo assume che le attività parallele
abbiano persone diverse: con una sola persona la durata reale si avvicina alla
somma del lavoro residuo (circa 94 giorni-uomo, compresi i task `Da decidere`).
Il dataset (fase 3) è il collo di bottiglia: anticiparlo accorcia tutto il piano.

## Importare il CSV in un Gantt

Il file è UTF-8, separato da virgole, con i campi di testo tra virgolette.

- **Excel**: Dati > Da testo/CSV, origine file `65001: Unicode (UTF-8)`,
  delimitatore virgola. Per le barre si aggiungono le colonne Inizio e Fine
  calcolate dalla data di partenza.
- **MS Project**: File > Apri, tipo CSV, importazione guidata con mappa dei
  campi: `Attivita` -> Nome, `Durata_gg` -> Durata (in giorni),
  `Predecessori` -> Predecessori (il separatore di elenco deve essere `;`),
  `Avanzamento_pct` -> % completamento, `Livello` -> Livello struttura.
- **GanttProject e altri tool**: importazione CSV con mappa delle colonne
  equivalente. Se il tool non accetta il campo Predecessori, le dipendenze si
  reinseriscono a mano dalla colonna WBS di questo documento.

## Aggiornare il piano

Modificare insieme il CSV e la tabella corrispondente di questo file,
mantenendo gli ID: i predecessori del CSV fanno riferimento agli ID, quindi una
riga nuova va aggiunta in fondo alla sua fase e gli ID successivi rinumerati.
