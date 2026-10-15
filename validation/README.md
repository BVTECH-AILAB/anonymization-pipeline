# Validazione dell'anonimizzazione

Misura quanto bene il servizio anonimizza documenti sanitari italiani. Si appoggia su un dataset
sintetico con ground truth e confronta più configurazioni (profili) dell'API. Il risultato è un
report Markdown con le metriche e l'elenco dei valori sfuggiti. La misura è geometrica: confronta
i riquadri applicati dal servizio con la posizione di ogni valore nel PDF originale.

Tutto è fuori da `src/` e non entra nell'immagine del servizio.

```
validation/
  PROMPT.md        prompt di generazione del dataset (in inglese, documenti in italiano)
  config.yaml      server, dataset, soglie, profili di parametri
  dataset/
    specs/<tipo>/<id>.json         spec generate dal prompt (sorgente del dataset)
    pdf/<tipo>/<id>.pdf            PDF nativi generati dalle spec
    ground_truth/<tipo>/<id>.json  ground truth generata dalle spec
    manifest.json                  indice dei documenti
  anonval/         generatore (spec, synthetic, render, build) e harness (locate, client,
                   scoring, metrics, report, run)
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
   - controlla che nessuna pagina sia trattata come scansione dal servizio
     (`anonymization_pipeline.pdf.pages.is_scanned`);
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
  disegnando il PDF una seconda volta con i valori marcati mascherati. Lo scoring le tollera:
  possono restare visibili.

## Validazione

### Avvio

Con il servizio avviato (`just up` o `just dev`):

```bash
just validation                                   # tutti i profili, tutto il dataset
just validation --profilo default                 # un profilo
just validation --config altra.yaml --output /tmp/run
```

Il terminale mostra l'avanzamento e, per ogni profilo, esito, recall, tasso di fuga e
precisione. **Codice di uscita**: 0 tutti i profili rispettano le soglie; 1 almeno un profilo
fallisce; 2 la run non è partita (configurazione, dataset o servizio non pronto).

### Configurazione (`config.yaml`)

- `server`: URL, intervallo di polling, timeout per documento (poi il job viene annullato).
- `dataset`: percorso e filtri (`tipi`, `difficolta`, `documenti`).
- `soglie`: `recall_min`, `tasso_fuga_max`, `precision_min`. Danno l'esito PASS/FAIL di ogni
  profilo; sono provvisorie finché PA-15 non è chiuso.
- `comune` e `profili`: parametri di `POST /anonymizations` (`enable`, `disable`: gruppi o
  categorie). Le categorie protette non si possono disattivare.

### Come si misura

Una pagina redatta dal servizio è un'immagine: non contiene più testo, quindi cercare i valori
nell'output non direbbe nulla. Lo scoring (`anonval/scoring.py`) lavora sulla geometria:

1. `anonval/locate.py` legge i caratteri del PDF originale con le loro coordinate (PDFium) e
   trova ogni occorrenza di ogni valore della ground truth con la ricerca tollerante di
   `anonval/text.py` (maiuscole, spazi, valori scritti un carattere per casella). È indipendente
   dallo strato di testo e dai rilevatori del servizio.
2. Il servizio restituisce i riquadri applicati (pagina, rettangolo, categoria, mai il valore).
3. Un carattere è coperto quando il suo centro cade in un riquadro della sua pagina.

| Metrica | Definizione |
|---|---|
| Valori attesi | coppie (valore, categoria) la cui categoria, o la sua categoria generica, è attiva nel profilo, nei documenti rilasciati |
| Recall | valori rilevati / attesi; rilevato: almeno metà dei caratteri di una sua occorrenza è coperta |
| Tasso di fuga | valori non protetti / attesi; protetto: le occorrenze non coperte per intero sono al massimo le sue copie non marcate (`unmarked`) |
| Precisione | riquadri che coprono un valore atteso / (gli stessi + falsi positivi); i riquadri che coprono solo valori di categorie disattivate sono contati a parte |
| Negativi violati | occorrenze di un negativo coperte da un riquadro di una famiglia vietata, fuori da un valore di quella famiglia |
| Valori nel testo rilasciato | valori forti (identificativi, nomi completi) coperti dai riquadri ma ancora presenti nel testo del PDF scaricato, cioè una censura non riuscita: deve essere 0. Un valore non coperto è già una fuga e, con la censura chirurgica, il suo testo resta sulla pagina |
| Documenti bloccati | documenti che il servizio non rilascia (verifica non superata, scansioni senza OCR): esclusi da recall e fughe, elencati con il motivo |

Il report scompone recall e fughe per categoria, difficoltà e tipo di documento, ed elenca i
valori sfuggiti con la copertura migliore, i falsi positivi, i negativi violati e i documenti
non rilasciati. Lo scoring è verificato simulando un servizio perfetto (riquadri esattamente sui
valori: recall 100%, nessuna fuga, nessun falso positivo) e uno che non censura nulla (recall 0%).

### Output

```
validation/results/<YYYYmmdd-HHMMSS>/
  report.md       report leggibile
  metriche.json   le stesse metriche
  raw/<profilo>/<id>.json   stato finale di ogni job (contatori e riquadri, nessun valore)
```

`validation/results/` è in `.gitignore`.

### Misure (15/10/2026)

Profilo `default`, 96 documenti, servizio in processo su CPU (24 core, torch a 4 thread).

| Configurazione | Recall | Fuga | Precisione | Negativi violati | s/documento |
|---|---:|---:|---:|---:|---:|
| `rules` + `labels` + propagazione | 74,5% | 30,7% | 91,6% | 11 | 0,42 |
| + NER `DeepMount00/Italian_NER_XXL_v2` | 86,9% | 17,4% | 87,9% | 72 | 1,60 |
| + NER `knowledgator/gliner-pii-base-v1.0` | 82,3% | 22,1% | 89,6% | 31 | 2,18 |
| + NER `urchade/gliner_multi_pii-v1` | 87,1% | 16,1% | 88,5% | 11 | 2,86 |
| default attuale: GLiNER multi PII + rilevamento visivo | 87,7% | 15,8% | 88,2% | 11 | 2,44 |

In nessuna configurazione restano valori nel testo dei PDF rilasciati e nessun documento viene
bloccato. Le fughe residue vengono soprattutto da strutture sanitarie e reparti (categorie attive di
default: PA-14), codici di laboratorio (`CODICE_CAMPIONE`, `ACCESSION_NUMBER`) e numeri di referto e
prenotazione. I negativi violati da `Italian_NER_XXL_v2` sono eponimi di classificazioni
istopatologiche (Ishak, Masson, Marsh, Oberhuber) non ancora in `eponyms.yaml`.

### Limiti noti

- I dati sono solo PDF nativi: il dataset non ha ancora varianti scansionate, quindi OCR e
  `ScanRedactor` sono coperti dai test ma non misurati qui.
- Un valore corto o comune può comparire anche fuori dal punto annotato; la ground truth ne
  conta le copie non marcate, tollerate.
- I confini dei dati sanitari (`DIAGNOSI`, `FARMACO`...) sono sfumati: basta metà di
  un'occorrenza coperta per contarli come rilevati.
