// Storico delle esecuzioni nel browser (IndexedDB): solo report e metriche, che non contengono valori,
// con l'impronta del documento e un'etichetta dell'operatore. Mai il nome del file ne' il testo.
import { openDB, type IDBPDatabase } from "idb";
import type { Metrics, Report } from "../api/tipi";
import { etichettaModelli, nomeEsito, nomeModalita } from "./nomi";

export interface Esecuzione {
  id: string;
  salvato: string; // ISO
  etichetta: string;
  impronta: string;
  modello: string;
  estrazione: string;
  modalita: string;
  pagine: number;
  totale_s: number;
  s_per_pagina: number | null;
  esito_verifica: string;
  report: Report;
  metriche: Metrics | null;
}

// le esecuzioni delle versioni precedenti (report con le chiavi in italiano, poi con un solo modello VLM)
// restano nei database vecchi e non si leggono
const DB = "anonpip-storico-2";

let db: Promise<IDBPDatabase> | null = null;

/** Id casuale (crypto.randomUUID esiste solo nei contesti sicuri: https o localhost). */
function idCasuale(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

function apri() {
  db ??= openDB(DB, 1, {
    upgrade(d) {
      d.createObjectStore("esecuzioni", { keyPath: "id" });
    },
  });
  return db;
}

export function daRisultato(report: Report, metriche: Metrics | null, etichetta = ""): Esecuzione {
  const totale = report.times_s.total ?? 0;
  return {
    id: idCasuale(),
    salvato: new Date().toISOString(),
    etichetta,
    impronta: report.document.sha256,
    modello: etichettaModelli(report.model),
    estrazione: metriche?.configuration.extraction ?? "",
    modalita: report.mode,
    pagine: report.document.pages,
    totale_s: totale,
    s_per_pagina: metriche?.throughput.s_per_page ?? (report.document.pages ? totale / report.document.pages : null),
    esito_verifica: report.verification.outcome,
    report,
    metriche,
  };
}

export async function salva(e: Esecuzione) {
  await (await apri()).put("esecuzioni", e);
}

export async function elenco(): Promise<Esecuzione[]> {
  const tutte = (await (await apri()).getAll("esecuzioni")) as Esecuzione[];
  return tutte.sort((a, b) => b.salvato.localeCompare(a.salvato));
}

export async function rinomina(id: string, etichetta: string) {
  const d = await apri();
  const e = (await d.get("esecuzioni", id)) as Esecuzione | undefined;
  if (e) await d.put("esecuzioni", { ...e, etichetta });
}

export async function elimina(id: string) {
  await (await apri()).delete("esecuzioni", id);
}

export async function svuota() {
  await (await apri()).clear("esecuzioni");
}

const COLONNE = [
  "salvato", "etichetta", "impronta", "modello", "estrazione", "modalita", "pagine", "totale_s", "s_per_pagina",
  "esito_verifica", "acquisizione_s", "estrazione_s", "rilevamento_s", "censura_s", "verifica_s",
  "vlm_chiamate", "vlm_token_in", "vlm_token_out", "latenza_p95_rilevamento_s", "valori_regole", "valori_etichette",
  "valori_ner", "valori_vlm", "cpu_s", "rss_mb",
] as const;

function cella(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV (separatore ;, per Excel in italiano) delle esecuzioni: colonne e modalita'/esito come nella tabella. */
export function csv(esecuzioni: Esecuzione[]): string {
  const righe = esecuzioni.map((e) => {
    const t = e.report.times_s;
    const vlm = Object.values(e.report.vlm);
    const f = e.report.detection.sources;
    const r: Record<(typeof COLONNE)[number], unknown> = {
      salvato: e.salvato, etichetta: e.etichetta, impronta: e.impronta.slice(0, 12), modello: e.modello,
      estrazione: e.estrazione, modalita: nomeModalita(e.modalita), pagine: e.pagine, totale_s: e.totale_s,
      s_per_pagina: e.s_per_pagina, esito_verifica: nomeEsito(e.esito_verifica),
      acquisizione_s: t.ingest, estrazione_s: t.extraction, rilevamento_s: t.detection,
      censura_s: t.redaction, verifica_s: t.verification,
      vlm_chiamate: vlm.reduce((s, x) => s + x.calls, 0),
      vlm_token_in: vlm.reduce((s, x) => s + x.tokens_in, 0),
      vlm_token_out: vlm.reduce((s, x) => s + x.tokens_out, 0),
      latenza_p95_rilevamento_s: e.metriche?.vlm.detection?.latency_s?.p95,
      valori_regole: f.rules, valori_etichette: f.labels, valori_ner: f.ner, valori_vlm: f.vlm,
      cpu_s: e.metriche?.resources.process_cpu_s, rss_mb: e.metriche?.resources.process_peak_rss_mb,
    };
    return COLONNE.map((c) => cella(r[c])).join(";");
  });
  return [COLONNE.join(";"), ...righe].join("\n");
}
