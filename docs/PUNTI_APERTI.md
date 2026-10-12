# Punti aperti - anonymization-pipeline

Decisioni ancora da prendere per il servizio di anonimizzazione. Ogni punto
indica cosa fa oggi il codice, le opzioni, i task del
[piano lavori](PIANO_LAVORI.md) che ne dipendono e chi deve decidere.
Quando un punto si chiude, si compila il campo **Decisione** con data e
riferimento (verbale, mail, ticket) e si aggiorna lo stato dei task collegati.

Stato rilevato il 12/10/2026 sul branch `feat/integrate-anonimizzatore-vlm`.

## Riepilogo

| ID | Area | Domanda | Task | Stato |
|---|---|---|---|---|
| PA-01 | Architettura | Serve un DB per la reversibilità? Dove si salvano documenti anonimizzati e mappe? | 1.4, 1.6, 4.15, 6.6 | Aperto |
| PA-02 | Integrazione | Come usare il modulo NER già funzionante dell'altra repo? | 1.7, 4.8 | Aperto |
| PA-03 | Sicurezza | Il servizio deve verificare le credenziali di chi lo chiama? | 1.7, 4.17 | Aperto |
| PA-04 | Modelli | Quale VLM e quali librerie? Prototipi su latenza e accuratezza | 4.1, M2 | In corso |
| PA-05 | Requisiti | Quali formati di input (PDF, scansione, immagine, DOCX) e di output? | 1.1, 4.3, 7.1 | Aperto |
| PA-06 | Processo | Come funziona la richiesta del documento originale con i dati sensibili? | 1.6, 4.15, 4.17 | Aperto |
| PA-07 | Requisiti | Valutare JEV | 1.8 | Aperto |
| PA-08 | Modelli | Il modello di verifica deve essere diverso da quelli di detection e redaction? | 4.1, M2 | Aperto |
| PA-09 | Processo | Dove vanno i documenti rifiutati (quarantena) e chi li rivede? | 1.5, 4.16 | Aperto |
| PA-10 | Integrazione | Chi chiama il servizio, in modo sincrono o asincrono, con quali volumi? | 1.7, 8.4 | Aperto |
| PA-11 | Governance | Il platform team approva le deroghe al template? | 1.7, 6.7 | Aperto |
| PA-12 | Architettura | Come scalare su più repliche? | 4.15, 4.18 | Aperto |
| PA-13 | Privacy | Modalità di default: pseudonimizzazione o anonimizzazione? | 1.1, 6.1 | Aperto |
| PA-14 | Requisiti | Vanno censurati anche i quasi-identificatori? | 1.1, 3.1, 5.3 | Aperto |
| PA-15 | Qualità | Quali soglie di accettazione per categoria? | 5.2, 5.3, M5 | Aperto |
| PA-16 | Requisiti | Come trattare firme, timbri, foto e codici nelle immagini? | 3.3 | Aperto |
| PA-17 | Compliance | Le licenze dei modelli consentono l'uso in una PA? | 6.5 | Aperto |
| PA-18 | Infrastruttura | Quale GPU è disponibile in staging e produzione? | 1.4, 7.4, 8.3 | Aperto |
| PA-19 | Deploy | Namespace e progetto Sonar ancora da assegnare | 2.6, 8.5 | Aperto |

PA-01 ... PA-08 sono i punti raccolti all'avvio; PA-09 ... PA-19 sono emersi
dalla ricognizione del codice e della documentazione.

---

## PA-01 Reversibilità, persistenza e latenza

**Domanda.** Serve un database per la reversibilità o basta il runtime? Quanta
latenza è accettabile per avere un documento anonimizzato? Dove vengono salvati
i documenti anonimizzati?

**Stato attuale.** Il servizio non conserva nulla. In modalità `pseudonym` la
corrispondenza tag -> valore viene cifrata (AES-256-GCM) in un file
`.mappa.enc` restituito al chiamante insieme al Markdown anonimizzato; il
ripristino (`POST /restore`) richiede Markdown, mappa e chiave. La chiave arriva
con la richiesta o da `ANONPIP_VAULT_KEY`. I job asincroni vivono in memoria e
si perdono al riavvio. Latenza misurata in `.metriche.json` (secondi per
pagina, p50/p95 per fase) ma senza un obiettivo.

**Opzioni.**
1. Runtime puro (oggi): il chiamante conserva Markdown e mappa. Nessun dato
   sanitario a riposo nel servizio; la custodia passa al chiamante.
2. Archivio gestito dal servizio: PostgreSQL (CNPG) per metadati e mappe
   cifrate, object storage per i documenti. Abilita ripristino su richiesta e
   audit, ma il servizio diventa titolare di dati pseudonimizzati.
3. Mappa in Vault, documenti presso il chiamante.

**Impatto.** 4.15 (se opzione 2 o 3), 6.6 (custodia chiave), 1.4 (obiettivo di
latenza), DPIA (6.2).

**Chi decide.** Committente e architettura MIA, con il DPO.

**Decisione.** -

## PA-02 Modulo NER esistente

**Domanda.** Come si usa il modulo NER già funzionante che vive in un'altra
repo?

**Stato attuale.** Il NER è locale: modello Hugging Face
`DeepMount00/Italian_NER_XXL_v2` su CPU (`detectors/ner/`), uno dei quattro
detector in unione con regole, etichette e VLM. Il template MIA prevede invece
che il testo destinato a un LLM passi da ner-anonymizer con chiamata
fail-closed.

**Opzioni.**
1. Sostituire il NER locale con una chiamata HTTP a ner-anonymizer
   (fail-closed: servizio giù -> 503).
2. Affiancarlo come quinto detector, mantenendo il NER locale.
3. Riusare dalla repo solo il modello o il vocabolario.

Da capire prima: API esposta, modello usato, latenza, categorie restituite e
come si mappano su `resources/categories.yaml`.

**Impatto.** 4.8; misura del guadagno di recall tramite l'harness (5.4).

**Chi decide.** Team di sviluppo, con i manutentori di ner-anonymizer.

**Decisione.** -

## PA-03 Credenziali dei chiamanti

**Domanda.** Il modulo deve verificare le credenziali prima di poter essere
usato?

**Stato attuale.** Nessun endpoint è autenticato. `GET /jobs` elenca i lavori
di tutti i chiamanti. L'unico segreto è la chiave della mappa, che protegge il
ripristino.

**Opzioni.**
1. Solo rete interna (NetworkPolicy), nessuna autenticazione applicativa.
2. JWT validato da WSO2 a monte (`X-JWT-Assertion`) e autorizzazione via authz,
   come gli altri servizi MIA.
3. API key per applicazione chiamante (service-to-service).

In ogni caso va valutato un ruolo separato per `/restore` e `/restore/detail`
(vedi PA-06) e la visibilità dei job per chiamante.

**Impatto.** 4.17, 8.4.

**Chi decide.** Architettura MIA e platform team.

**Decisione.** -

## PA-04 Modello VLM e librerie

**Domanda.** Quale VLM e quali librerie usare? Servono prototipi da
confrontare su latenza e accuratezza.

**Stato attuale.** Un modello per fase, scelto da `resources/models.yaml`. Nella
copia di lavoro: estrazione `glm-ocr`, detection e redaction `qwen3.8:27b`
(solo testo, sostituisce `granite4.2:8b`), verifica `gemma4:26b-a4b-it-qat`.
Le pagine native vanno a docling. Il frontend (Storico, Dashboard) confronta
già le configurazioni sulle metriche prestazionali; manca la misura
dell'accuratezza.

**Opzioni.** Matrice di prototipi per fase (modelli OCR/VLM, docling contro
VLM sulle pagine native, dimensione del modello) valutata con l'harness 5.4
sul dataset della fase 3.

Vincoli da tenere presenti:
- con `OLLAMA_MAX_LOADED_MODELS=1` tre modelli diversi vengono scambiati in
  VRAM a ogni cambio di fase;
- un modello da 27B non sta nella GPU da 8 GB a cui si riferiscono le note del
  catalogo (vedi PA-18);
- un modello di detection senza vision non riceve l'immagine della pagina.

**Impatto.** 4.1, M2, 6.5 (licenze), 8.3.

**Chi decide.** Team di sviluppo, sui risultati dei prototipi.

**Decisione.** -

## PA-05 Formati di input e di output

**Domanda.** Quali formati accettare in ingresso (PDF, scansione, immagine,
DOCX) e quale output produrre?

**Stato attuale.** Input: PDF nativi e scansionati, immagini (png, jpg, tif,
bmp, webp), testo (txt, md, csv, json, xml, html, eml). DOCX e Office sono
rifiutati con "Formato non supportato". Output: Markdown anonimizzato, report,
metriche, mappa cifrata, opzionalmente trascrizione in chiaro; consegna in ZIP
o JSON. Non si produce un PDF o un'immagine con le zone coperte.

**Opzioni.**
- Input: aggiungere DOCX (conversione con docling o estrazione del testo),
  oppure chiedere al chiamante di convertire in PDF.
- Output: restare sul Markdown (adatto all'indicizzazione RAG) o produrre anche
  un PDF redatto con layout preservato (molto più costoso: richiede le
  coordinate di ogni valore).

**Impatto.** 4.3, 7.1, dataset (3.2).

**Chi decide.** Committente, in base all'uso a valle del documento.

**Decisione.** -

## PA-06 Richiesta del documento originale

**Domanda.** Come funziona la richiesta del documento originale con i dati
sensibili?

**Stato attuale.** Il servizio non conserva l'originale. Il ripristino
ricostruisce il testo in chiaro da Markdown + mappa + chiave; l'originale
(PDF, immagine) resta solo presso chi lo ha caricato. Non c'è audit di chi
esegue un ripristino.

**Da definire.** Chi può chiedere l'originale e per quale finalità; se
basta il testo ricostruito o serve il file originale; dove si trova la chiave;
quale traccia di audit si conserva (senza dati personali nei log).

**Impatto.** 4.15, 4.17, DPIA (6.2).

**Chi decide.** Committente e DPO.

**Decisione.** -

## PA-07 Valutare JEV

**Domanda.** Valutare JEV.

**Da chiarire.** Che cosa si intende con JEV e in quale ambito (tecnica,
strumento, requisito, valutazione esterna), poi quali criteri usare.

**Impatto.** 1.8; da collegare ad altri task dopo il chiarimento.

**Chi decide.** Da assegnare.

**Decisione.** -

## PA-08 Modello di verifica diverso

**Domanda.** Il modello di verifica deve essere diverso da quelli di detection
e redaction?

**Stato attuale.** Già configurabile per fase. Oggi la verifica usa
`gemma4:26b-a4b-it-qat`, diverso da detection e redaction. La verifica rilegge
solo il testo (Markdown censurato contro Markdown in chiaro) e non riceve
l'immagine, quindi la capacità vision del modello resta inutilizzata. In
modalità `block` (default) un residuo rifiuta il documento; se la verifica non
può girare il documento è rifiutato.

**Considerazioni.**
- A favore di un modello diverso: errori meno correlati, un valore sfuggito
  alla detection ha più probabilità di essere colto.
- Contro: un modello in più da scaricare e da scambiare in VRAM, quindi più
  latenza.
- Da misurare: residui trovati in verifica con modello uguale e con modello
  diverso, sullo stesso dataset.

**Impatto.** 4.1, M2.

**Chi decide.** Team di sviluppo, sui risultati dei prototipi.

**Decisione.** -

## PA-09 Quarantena dei documenti rifiutati

**Domanda.** Che cosa succede a un documento rifiutato?

**Stato attuale.** Il rifiuto (residui in verifica, invariante violata,
verifica non disponibile) restituisce HTTP 422 senza valori e registra
l'evento `document_rejected` con il motivo. Il documento non viene conservato.

**Opzioni.** Restituzione al chiamante (oggi), oppure area di quarantena con
revisione umana, retention breve e notifica.

**Impatto.** 1.5, 4.16; collegato a PA-01 (dove conservare).

**Chi decide.** Committente e DPO.

**Decisione.** -

## PA-10 Chiamanti e modalità d'uso

**Domanda.** Chi chiama il servizio e come?

**Stato attuale.** `docs/DEPLOY_HANDOVER.md` riporta "Chi lo chiama: da
definire". Sono disponibili sia `POST /anonymize` (sincrono) sia `POST /jobs`
(asincrono con polling). Il frontend web è uno strumento di prova e
revisione.

**Da definire.** Candidati: validation-flow-gitlab (caricamento nella knowledge
base), ai-platform, medici tramite fe. Per ciascuno: volumi attesi, dimensione
dei documenti, sincrono o asincrono, che cosa fa dell'output.

**Impatto.** 8.4, 1.4 (latenza), PA-03, PA-12.

**Chi decide.** Architettura MIA.

**Decisione.** -

## PA-11 Deroghe al template

**Domanda.** Il platform team approva le deroghe al template?

**Stato attuale.** In `docs/DEPLOY_HANDOVER.md`, "Deroghe al template (da
approvare)":
- niente llm-gateway: il VLM gira on-prem su Ollama in una rete isolata, nessun
  dato esce dal cluster;
- niente gate su ner-anonymizer (vedi PA-02);
- job in memoria, quindi una sola replica (vedi PA-12).

**Impatto.** 6.7, 8.5.

**Chi decide.** Platform team.

**Decisione.** -

## PA-12 Scalabilità

**Domanda.** Come si scala oltre una replica?

**Stato attuale.** I job vivono nella memoria del pod: con più repliche serve
session affinity oppure una persistenza dei job. Il limite di concorrenza VLM
vale per documento, quindi le chiamate parallele al backend possono arrivare a
`vlm_concurrency x max_concurrent_jobs`.

**Opzioni.** Una replica dimensionata sulla GPU (oggi); session affinity;
coda persistente (PostgreSQL o broker) condivisa tra repliche.

**Impatto.** 4.15, 4.18, 8.3.

**Chi decide.** Team di sviluppo e platform team, dopo PA-10.

**Decisione.** -

## PA-13 Modalità di default: pseudonimizzazione o anonimizzazione

**Domanda.** Quale modalità usare di default?

**Stato attuale.** Default `pseudonym`: tag numerati e mappa cifrata che
consente il ripristino. Ai fini del GDPR un dato pseudonimizzato resta un dato
personale. Le modalità `tag` e `redaction` non producono mappa.

**Impatto.** 6.1, 6.2, retention (PA-01).

**Chi decide.** Committente e DPO.

**Decisione.** -

## PA-14 Quasi-identificatori

**Domanda.** Vanno censurati anche i quasi-identificatori?

**Esempi.** Date (nascita, ricovero), luoghi, strutture, professioni, patologie
rare: da soli non identificano, ma combinati sì.

**Stato attuale.** Le categorie sono in `resources/categories.yaml` e si
possono attivare o disattivare per richiesta.

**Impatto.** 3.1 (schema di annotazione), 5.3, utilità clinica del testo
anonimizzato.

**Chi decide.** Committente e DPO.

**Decisione.** -

## PA-15 Soglie di accettazione

**Domanda.** Quali valori minimi di qualità servono per il rilascio?

**Da definire.** Recall minima per categoria (più alta per gli identificatori
diretti), tolleranza alla over-redaction, tasso massimo di documenti rifiutati,
latenza massima per pagina.

**Impatto.** 5.2, 5.3, M5.

**Chi decide.** Committente, su proposta del team.

**Decisione.** -

## PA-16 Elementi visivi

**Domanda.** Come trattare firme, timbri, foto, loghi, codici a barre e QR?

**Stato attuale.** L'estrazione li sostituisce con segnaposto nel Markdown
(`[FIRMA]`, `[TIMBRO: ...]`, `[FOTO]`, `[QR_CODE]` e altri, in
`resources/default.yaml`); il testo di timbri e firme leggibili viene
trascritto e passa dalla detection. L'immagine originale non viene modificata.

**Impatto.** 3.3 (varianti nel dataset), PA-05 (output redatto).

**Chi decide.** Committente.

**Decisione.** -

## PA-17 Licenze dei modelli

**Domanda.** Le licenze dei modelli e delle librerie consentono l'uso in una
pubblica amministrazione?

**Da verificare.** Modelli del catalogo (GLM-OCR, Gemma, Qwen), NER
`DeepMount00/Italian_NER_XXL_v2`, modelli docling, dipendenze Python e npm.

**Impatto.** 6.5, M2.

**Chi decide.** Ufficio legale o acquisti del committente.

**Decisione.** -

## PA-18 Hardware target

**Domanda.** Quale GPU è disponibile in staging e produzione (OKE)?

**Stato attuale.** `docs/DEPLOY_HANDOVER.md` richiede 1 GPU e 16 Gi per il
VLM. Le note del catalogo modelli fanno riferimento a una GPU da 8 GB.

**Impatto.** 1.4, 4.1 (dimensione dei modelli), 7.4, 8.3.

**Chi decide.** Platform team.

**Decisione.** -

## PA-19 Namespace e progetto Sonar

**Domanda.** Assegnare namespace e chiave del progetto Sonar.

**Stato attuale.** "Namespace proposto" vuoto in `docs/DEPLOY_HANDOVER.md`;
`sonar-project.properties` contiene ancora `CHANGEME`.

**Impatto.** 2.6, 8.5.

**Chi decide.** Platform team.

**Decisione.** -
