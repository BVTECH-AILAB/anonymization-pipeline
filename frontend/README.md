# frontend

Interfaccia web standalone di anonymization-pipeline: React + TypeScript (Vite), servita da nginx. Parla
solo con l'API, tramite `/api`: proxy di nginx verso `API_UPSTREAM` (default `http://app:8000`, il servizio
del compose), proxy di Vite in sviluppo.
Entrambi tolgono il prefisso, quindi `/api/jobs` arriva all'API come `/jobs`.

Nessun token: l'API non ha autenticazione propria e l'interfaccia non invia l'header `Authorization`.

## Avvio

Dalla radice del repository, con lo stack compose:

```sh
just up      # interfaccia su http://localhost:8080, API su http://localhost:8000
just down
```

Sviluppo, con l'API in locale su `:8000` (`just dev` dalla radice) o altrove con `ANONPIP_API_URL`:

```sh
npm ci
npm run dev          # http://localhost:5173, /api -> ANONPIP_API_URL (predefinito http://localhost:8000)
npm test             # vitest
npx tsc --noEmit     # solo il controllo dei tipi
npm run build        # tsc + vite build in dist/
```

Per esempio `ANONPIP_API_URL=http://localhost:8001 npm run dev`.

## Struttura

```
src/
  api/          client.ts (fetch verso /api, errori {code, detail}), tipi.ts (contratto dell'API, in inglese)
  stato/        sessione.tsx: chiave della mappa, documenti e lavori della scheda, polling
  pagine/       Carica, Confronto, Dashboard, Storico, Ripristino, Stato
  componenti/   VistaOriginale (pdf.js), VistaTesto (Markdown reso o grezzo con evidenziazione),
                Grafici (Recharts), BannerStato, Impostazioni
  lib/          pagine.ts (divisione su <!-- pagina N -->), evidenzia.ts (intervalli e marcatori
                per il Markdown reso), nomi.ts (nomi italiani dei valori dell'API), revisione.ts,
                storico.ts (IndexedDB, CSV)
  test/         vitest
```

## Endpoint usati

| Endpoint | Uso |
|---|---|
| `GET /readyz` | Banner e pagina Stato. 200 se il servizio è pronto, 503 se non lo è, con lo stesso corpo: l'interfaccia mostra lo stato in entrambi i casi |
| `GET /models` | Menu Modello VLM (modello predefinito ed elenco del backend) |
| `GET /categories` | Gruppi di categorie da abilitare o disabilitare; categorie per la revisione |
| `POST /jobs` | Un lavoro per file (202). Campi: `file`, `mode` (`pseudonym`, `tag`, `redaction`), `model`, `verification` (`off`, `report`, `fix`, `block`), `extraction` (`docling`, `vlm`), `enable`, `disable` (gruppi separati da virgole), `transcript`, `key` (solo in modalità pseudonimo) |
| `GET /jobs/{id}` | Avanzamento del lavoro: stato, fase, iterazione, chiamate al VLM |
| `GET /jobs/{id}/result` | Risultato in JSON; con `?format=zip` lo ZIP da scaricare |
| `DELETE /jobs/{id}` | Rimuovi e Svuota sessione |
| `POST /restore/detail` | Testo in chiaro con le posizioni dei tag. Campi: `markdown`, `map` (file) e `key` |

Gli errori hanno il corpo `{code, detail, ...}`: l'interfaccia mostra `detail`, il messaggio in italiano.
Stati, fasi, modalità, esiti e avvisi arrivano in inglese e sono mostrati in italiano (`lib/nomi.ts`).

## Flusso

1. **Carica**: `GET /models` e `GET /categories` per le opzioni; `POST /jobs` per ogni file con le opzioni
   scelte; polling di `GET /jobs/{id}` ogni 2 s; a lavoro completato `GET /jobs/{id}/result` e
   salvataggio di report e metriche nello storico.
2. **Confronto**: il Markdown censurato e le posizioni dei tag (`tags`) vengono dal risultato. Il testo in
   chiaro viene da `transcript` + `occurrences` (se la richiesta l'ha chiesta), altrimenti da
   `POST /restore/detail` con la mappa del risultato (`encrypted_map_b64`) e la chiave della sessione.
   Pagine divise sui separatori `<!-- pagina N -->`; l'originale è il `File` caricato, mostrato nel
   browser (pdf.js per i PDF), mai rimandato al server.
3. **Revisione**: clic su un tag (censura errata, categoria sbagliata) o selezione di un testo nel
   censurato in vista grezza (valore sfuggito). `<impronta>.revisione.json` contiene tipo, posizione e
   lunghezza nel Markdown censurato, pagina, categorie e note: nessun valore.
4. **Dashboard**: report e metriche di un documento della sessione, di un'esecuzione dello storico o di file
   aperti dal disco (`.report.json`, `.metriche.json` o lo ZIP del risultato) prodotti da questa versione
   del servizio.
5. **Ripristino**: `POST /restore/detail` con il `.anon.md`, la `.mappa.enc` e la chiave.

I file prodotti mantengono i nomi di sempre: `<nome>.anon.md`, `.report.json`, `.mappa.enc`,
`.metriche.json`, `.trascrizione.md`.

## Riservatezza

- La chiave della mappa resta solo in `sessionStorage` (si perde chiudendo la scheda), mai in
  `localStorage`. "Svuota sessione" cancella anche i lavori sul server.
- Il testo in chiaro e l'originale restano in memoria nella scheda; non vengono salvati.
- Gli id dei lavori restano nella scheda: dopo un ricaricamento i risultati si rileggono dall'API
  (finché non scadono), l'originale non c'è più.
- Lo storico (IndexedDB) contiene solo report e metriche, con l'impronta del documento: niente nome del
  file, che spesso contiene il nome del paziente. Le esecuzioni salvate dalla versione precedente
  dell'interfaccia restano nel loro database e non vengono lette.
- nginx: CSP restrittiva (solo stessa origine), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`;
  richieste fino a 101 MB (`/restore` riceve due file da `ANONPIP_MAX_UPLOAD_MB`, 50 MB).

## Grafici

Recharts con i colori della palette di riferimento (slot categoriali in ordine fisso, validati per
daltonismo in chiaro e in scuro), legenda nel colore del testo, dati anche in tabella. Tema chiaro e scuro
dalle preferenze del sistema.
