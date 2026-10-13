# Validazione dell'anonimizzazione

Misura quanto bene il servizio anonimizza documenti sanitari italiani. Si appoggia su un dataset
sintetico con ground truth e confronta più configurazioni (profili) dell'API. Il risultato è un
report Markdown con le metriche e l'elenco dei valori sfuggiti.

Tutto è fuori da `src/` e non entra nell'immagine del servizio.

```
validation/
  PROMPT.md        prompt di generazione del dataset (in inglese, documenti in italiano)
  config.yaml      server, dataset, soglie, profili di parametri
  validate.sh      avvio della validazione (lo usa `just validation`)
  dataset/
    specs/<tipo>/<id>.json         spec generate dal prompt (sorgente del dataset)
    pdf/<tipo>/<id>.pdf            PDF nativi generati dalle spec
    ground_truth/<tipo>/<id>.json  ground truth generata dalle spec
    manifest.json                  indice dei documenti
  anonval/         generatore (spec, render, build) e harness (client, scoring, report, run)
  tests/           test unitari (girano anche con `just test`)
  results/         output delle run (non versionato)
```

## Dataset

**8 tipologie x 12 documenti = 96 PDF nativi.** Per ogni tipologia ci sono 4 documenti facili,
4 medi e 4 complessi.

| Tipo | Documento |
|---|---|
| `referto_laboratorio` | Referto di laboratorio analisi |
| `test_genetico` | Referto di test genetico |
| `biopsia_patologica` | Referto istologico / biopsia |
| `lettera_dimissione` | Lettera di dimissione, anche da Terapia Intensiva |
| `nota_ambulatoriale` | Nota di visita ambulatoriale |
| `autorizzazione_preventiva` | Modulo di autorizzazione preventiva ASL |
| `referto_radiologico` | Referto radiologico |
| `referto_esterno` | Referto medico esterno (privato, altra regione, via fax) |

| | facile | media | complessa |
|---|---|---|---|
| Pagine | 1 | 2-3 | 3-5 |
| Valori identificativi distinti (gruppi `sanitari` e `particolari` esclusi) | 8-18 | 15-35 | 30 o più |
| Contenuto | layout a una colonna, dati etichettati | tabelle con dati, intestazione ripetuta, date in più formati, familiari | due colonne, caselle per carattere, timbri ruotati, forme parziali dei nomi, eponimi come negativi, categorie particolari |

I limiti della tabella sono controllati dal generatore.

**I dati sono tutti sintetici.**
- Persone, strutture, recapiti (domini `.example.it`) e identificativi sono inventati.
- Codici fiscali, IBAN, partite IVA e numeri di tessera sanitaria sono calcolati con un checksum
  valido a partire da dati fittizi (`anonval/synthetic.py`), perché i rilevatori del servizio
  verificano i checksum.
- Questi codici non appartengono a persone reali. Si trovano solo qui, mai in `tests/fixtures`.

### Come nasce un documento

1. **Spec.** Un LLM, o un agente, esegue `PROMPT.md` e scrive una spec JSON per documento. La
   spec descrive:
   - lo stile: font, colore, margini, eventuale riga di intestazione fax;
   - la testata, l'intestazione ripetuta e il piè di pagina;
   - una sequenza di blocchi di layout: riquadro paziente, tabelle, bande di sezione, codici a
     barre e QR, caselle per carattere, checkbox, firme, timbri, due colonne, note.

   Ogni dato personale o sanitario è scritto in linea come `[[CATEGORIA:valore]]`, con le
   categorie di `src/anonymization_pipeline/resources/categories.yaml`. I valori con checksum si
   scrivono come `[[CODICE_FISCALE:@persona]]`, `[[IBAN:@etichetta]]` e simili, e li calcola il
   generatore.
2. **PDF e ground truth.** `just validation-dataset` (`anonval/build.py`) fa questi passi per ogni
   spec:
   - la valida;
   - risolve i marcatori;
   - disegna il PDF con reportlab (deterministico: stessa spec, stessi byte);
   - scrive la ground truth;
   - controlla che ogni valore sia nel testo estratto dal PDF;
   - controlla che ogni pagina sia nativa per il classificatore del servizio
     (`anonymization_pipeline.ingest`);
   - controlla che i limiti della difficoltà e le quantità (4 per tipo e difficoltà) siano
     rispettati.

```bash
just validation-dataset                          # tutto il dataset, scrive manifest.json
just validation-dataset --solo <id> [<id> ...]   # solo alcune spec (manifest invariato)
```

Per aggiungere o rigenerare documenti: esegui `PROMPT.md` con un LLM per la tipologia voluta,
salva le spec in `dataset/specs/<tipo>/` e lancia `just validation-dataset`.

### Ground truth

Un file per documento, con una voce per ogni coppia (valore, categoria):

```json
{
  "id": "referto_laboratorio_media_01",
  "type": "referto_laboratorio",
  "difficulty": "media",
  "pages": 2,
  "sha256": "...",
  "entities": [
    {"value": "Giulia Bianchi Ferraro", "category": "PERSONA", "group": "anagrafica",
     "family": "PERSONA", "mentions": 3, "locations": ["running_header", "patient_box"], "pages": [1, 2],
     "unmarked": 0}
  ],
  "negatives": [{"value": "Parkinson", "forbidden_categories": ["PERSONA"]}]
}
```

- `locations`: dove compare nel layout (testata, riquadro paziente, tabella, timbro, piè di
  pagina...). Il report calcola le metriche anche per posizione.
- `negatives`: stringhe che sembrano dati personali ma non lo sono, come gli eponimi
  ("morbo di Parkinson"). Il servizio non deve censurarle con le categorie indicate.
- `unmarked`: copie dello stesso testo rimaste senza marcatore. Per esempio "rosa" come colore
  accanto a "Rosa" come nome, oppure "M" del sesso dentro altre sigle. Il generatore le conta
  disegnando il PDF una seconda volta con i valori marcati mascherati. Lo scoring le tollera
  nel Markdown censurato.

## Validazione

### Prerequisiti

- Servizio avviato (`just up`, oppure `just up-gpu`) con `ANONPIP_ALLOW_TRANSCRIPT=true`. Lo
  script chiede la trascrizione in chiaro, che è consentita solo in `local` e `staging`, mai in
  `prod`.
- docling installato nel servizio (immagine Docker), altrimenti le pagine native finiscono al
  VLM: il report lo segnala alla voce "Pagine passate al VLM".
- Per i profili con `mode: pseudonym`: la chiave nella variabile `ANONVAL_KEY`, per esempio in
  `.env`. Non va mai scritta in `config.yaml`.

### Avvio

```bash
just validation                                   # tutti i profili, tutto il dataset
just validation --profilo completo --tipo referto_laboratorio --difficolta facile
just validation --documento referto_radiologico_complessa_02 --url http://localhost:8000
just validation --riprendi validation/results/20261013-101500   # completa una run interrotta
validation/validate.sh --help                      # stesso comando, senza just
```

Opzioni (tutte ripetibili tranne le ultime quattro): `--profilo`, `--tipo`, `--difficolta`,
`--documento`, `--url`, `--concorrenza`, `--config`, `--riprendi`, `--output`.

Durante la run il terminale mostra:
- la barra complessiva (profili x documenti) con tempo trascorso e stima del tempo residuo;
- la barra del profilo in corso;
- per ogni documento in elaborazione: fase della pipeline, posizione in coda e secondi
  trascorsi;
- per ogni documento finito una riga: `OK` / `RESPINTO` / `ERRORE` / `TIMEOUT`, recall, fughe,
  falsi positivi, durata.

**Codice di uscita**:
- 0: tutti i profili rispettano le soglie;
- 1: almeno un profilo fallisce;
- 2: la run non è partita (configurazione, dataset o servizio).

### Configurazione (`config.yaml`)

- `server`: URL, intervallo di polling, timeout per documento, job inviati insieme, nome della
  variabile con la chiave.
- `dataset`: percorso e filtri (`tipi`, `difficolta`, `documenti`).
- `soglie`: `recall_min`, `tasso_fuga_max`, `precision_min`, `f1_min`. Danno l'esito PASS/FAIL
  di ogni profilo. Sono valori provvisori finché il punto aperto PA-15 non è chiuso.
- `comune`: parametri validi per tutti i profili.
- `profili`: lista di profili con `nome`, `descrizione` e i parametri di `POST /jobs`. Ogni
  campo assente lascia il default del server.

| Parametro | Valori |
|---|---|
| `mode` | `pseudonym`, `tag`, `redaction` |
| `verification` | `off`, `report`, `block` (con `block` un documento con residui viene respinto e non ha metriche) |
| `extraction` | `docling`, `vlm` |
| `model_extraction`, `model_detection`, `model_verification` | nomi di `GET /models` |
| `enable`, `disable` | gruppi o categorie di `GET /categories` (`disable` vince) |
| `detectors` | insieme dei rilevatori attivi tra `vlm`, `rules`, `labels`, `ner`, `eponyms` |
| `layout` | `true`, `false` |

La configurazione viene controllata prima di inviare documenti:
- enum, categorie e rilevatori;
- modelli presenti nel catalogo e nel backend;
- chiave disponibile per i profili `pseudonym`.

### Metriche

L'unità di misura è il **valore della ground truth**: una coppia (valore, categoria) per
documento.

| Metrica | Definizione |
|---|---|
| Valori attesi | valori la cui categoria, o la categoria generica della sua famiglia, è attiva nel profilo e che compaiono nella trascrizione |
| Recall | valori rilevati / attesi. Rilevato significa che le occorrenze censurate coprono almeno metà di una sua occorrenza. |
| Tasso di fuga | valori ancora in chiaro nel Markdown censurato / attesi. I valori con meno di 3 caratteri alfanumerici contano come protetti quando sono coperte tante occorrenze quante ne indica la ground truth. |
| Precision | occorrenze censurate che si sovrappongono a un valore atteso / occorrenze censurate. Le censure di valori con categoria disattivata sono contate a parte. |
| Precision con categoria | come la precision, ma la categoria assegnata deve essere della stessa famiglia del valore |
| F1 | media armonica di precision e recall |
| Categoria esatta / famiglia | tra i valori rilevati, quelli con la stessa categoria della ground truth o della stessa famiglia (`generic` di `categories.yaml`) |
| Documenti con fughe | documenti con almeno un valore rimasto in chiaro |
| Negativi violati | negativi noti censurati con una categoria vietata |
| Valori persi dall'estrazione | valori assenti dalla trascrizione: problema di estrazione, esclusi da recall e fughe |
| Verificatore | documenti con fughe segnalate e non segnalate dai residui di `verification=report`, falsi allarmi |

Il report scompone le metriche per difficoltà, tipo x difficoltà, gruppo, categoria e posizione
nel layout. Elenca inoltre:
- i valori rimasti in chiaro;
- i valori persi;
- i falsi positivi;
- i negativi violati;
- i documenti respinti o in errore.

Quando ci sono più profili aggiunge un confronto con il primo.

### Output

```
validation/results/<YYYYmmdd-HHMMSS>/
  report.md       report leggibile
  metriche.json   le stesse metriche, più i punteggi di ogni documento
  server.json     versione, categorie attive, modelli e configurazione del servizio
  raw/<profilo>/<id>.json   risultato di ogni documento (riusato da --riprendi)
```

I file di `raw/` contengono il testo dei documenti sintetici in chiaro e la loro anonimizzazione.
Stanno solo in locale (`validation/results/` è in `.gitignore`).

### Limiti noti

- Il confronto avviene sui valori, non sugli offset. Il servizio lavora sul Markdown estratto da
  docling, che può riformattare il testo; la ricerca ignora maiuscole, spazi, trattini, escape
  Markdown e, per valori di almeno 6 caratteri, anche separatori tra singoli caratteri (caselle).
- Una stringa molto corta o molto comune (per esempio un `ESAME_VALORE` come `9`) può comparire
  anche fuori dal punto annotato. Il prompt chiede di evitarlo e la ground truth ne conta le
  copie non marcate (`unmarked`), tollerate dallo scoring. I numeri isolati restano comunque
  meno affidabili delle altre categorie.
- Prima di cercare le fughe, i sostituti (tag `[CATEGORIA_n]`, simbolo di redazione, pseudonimi)
  vengono cancellati dal Markdown censurato usando gli offset `tags` del risultato. Così un
  valore uguale al nome di una categoria ("gravidanza") non risulta trapelato.
- Lo scoring è verificato in due modi:
  - simulando un servizio perfetto, che censura esattamente la ground truth sul testo dei PDF:
    recall 100%, nessuna fuga, precision 100%;
  - simulando un servizio che non censura nulla: recall 0%.
- I confini dei dati sanitari (`DIAGNOSI`, `FARMACO`...) sono per natura sfumati: basta una
  sovrapposizione parziale per contarli come rilevati.
