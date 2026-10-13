// Client dell'API di validazione (validation/anonval/api.py): solo nello stack locale, via /validazione-api
// (proxy di nginx o di Vite). Nel cluster il percorso non esiste e la sezione resta nascosta.
import { ErroreApi } from "./client";
import type { ExtractionEngine, Mode, Rilevatore, VerificationMode } from "./tipi";

export const BASE_VALIDAZIONE = "/validazione-api";

/** Parametri di POST /jobs di un profilo (validation/config.yaml); null o assente = default del servizio. */
export interface Parametri {
  mode?: Mode | null;
  verification?: VerificationMode | null;
  extraction?: ExtractionEngine | null;
  model_extraction?: string | null;
  model_detection?: string | null;
  model_verification?: string | null;
  enable?: string[] | null;
  disable?: string[] | null;
  detectors?: Rilevatore[] | null;
  layout?: boolean | null;
}

export interface Profilo extends Parametri {
  nome: string;
  descrizione: string;
}

export interface Soglie {
  recall_min: number;
  tasso_fuga_max: number;
  precision_min: number;
  f1_min: number;
}

export interface ConfigValidazione {
  server: { base_url: string; poll_interval_s: number; timeout_documento_s: number; concorrenza: number; chiave_env: string };
  dataset: { percorso: string; tipi: string[]; difficolta: string[]; documenti: string[] };
  soglie: Soglie;
  comune: Parametri;
  profili: Profilo[];
}

export interface DocumentoDataset {
  id: string;
  type: string;
  difficulty: string;
  pages: number;
  entities: number;
}

/** GET /config: config.yaml, i documenti del dataset e se il servizio ha una chiave per pseudonym. */
export interface ConfigIniziale {
  config: ConfigValidazione;
  documents: DocumentoDataset[];
  key_available: boolean;
}

export type StatoRun = "in_corso" | "completata" | "annullata" | "interrotta" | "sconosciuto";

export interface EsitoProfilo {
  nome: string;
  esito: boolean | null;
  interrotto: string | null;
  soglie: Record<string, boolean | null>;
  recall: number | null;
  tasso_fuga: number | null;
  precision: number | null;
  f1: number | null;
  documenti: { totali: number; completati: number; respinti: number; errori: number };
  documenti_con_fughe: number;
  negativi_violati: number;
  /** PDF con layout: null se nessun profilo l'ha chiesto. */
  layout: {
    documenti_verificati: number;
    pdf_non_prodotti: number;
    valori_verificati: number;
    fughe: number;
    tasso_fuga: number | null;
    documenti_con_fughe: number;
  } | null;
  tempi_s: { medio: number | null; p95: number | null; totale: number };
  per_categoria: Record<string, { attesi: number; fughe: number; tasso_fuga: number | null }>;
}

export interface RunInElenco {
  id: string;
  stato: StatoRun;
  profili: string[];
  dataset: { tipi?: string[]; difficolta?: string[]; documenti?: string[] };
  esiti: Record<string, boolean | null>;
}

/** Avanzamento della run in corso (solo per la run attiva del processo). */
export interface Avanzamento {
  profiles: string[];
  profile: string | null;
  documents: number;
  total: number;
  completed: number;
  per_profile: Record<string, number>;
  running: { profile: string; document: string; status: string | null; phase: string | null; position: number | null; seconds: number }[];
  finished: {
    profile: string;
    document: string;
    status: string;
    recall: number | null;
    leaks: number;
    false_positives: number;
    layout_leaks: number | null; // null: PDF con layout non chiesto; -1: chiesto ma non prodotto
    seconds: number | null;
    error: string | null;
  }[];
  started: string;
  error: string | null;
}

export interface Run {
  id: string;
  stato: StatoRun;
  live: Avanzamento | null;
  profili: EsitoProfilo[];
}

/** Errore di una risposta non riuscita: `detail` testuale, oppure la lista degli errori dei campi (422). */
export async function erroreValidazione(r: Response): Promise<ErroreApi> {
  let detail: unknown = null;
  try {
    detail = ((await r.json()) as { detail?: unknown }).detail;
  } catch {
    /* corpo non JSON */
  }
  if (Array.isArray(detail)) {
    const campi = detail.map((d: { loc?: (string | number)[]; msg?: string }) =>
      `${(d.loc ?? []).filter((x) => x !== "body").join(".")}: ${d.msg ?? ""}`);
    return new ErroreApi(r.status, campi.join("; "));
  }
  return new ErroreApi(r.status, typeof detail === "string" ? detail : `${r.status} ${r.statusText}`);
}

async function chiama(percorso: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(BASE_VALIDAZIONE + percorso, init);
  if (!r.ok) throw await erroreValidazione(r);
  return r;
}

const json = async <T>(p: Promise<Response>): Promise<T> => (await p).json() as Promise<T>;
const corpo = (x: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(x),
});
const percorsoRun = (id: string) => `/runs/${encodeURIComponent(id)}`;

/** Il servizio di validazione risponde? Nel cluster nginx restituisce index.html (non JSON): no. */
export async function disponibile(): Promise<boolean> {
  try {
    const r = await fetch(BASE_VALIDAZIONE + "/health");
    if (!r.ok) return false;
    const j: unknown = await r.json();
    return !!j && typeof j === "object" && (j as { status?: string }).status === "ok";
  } catch {
    return false;
  }
}

export const configIniziale = () => json<ConfigIniziale>(chiama("/config"));
export const avvia = (config: ConfigValidazione, chiave: string | null) =>
  json<{ id: string }>(chiama("/runs", corpo({ config, key: chiave || null })));
export const elenco = () => json<RunInElenco[]>(chiama("/runs"));
export const run = (id: string) => json<Run>(chiama(percorsoRun(id)));
export const report = async (id: string) => (await chiama(`${percorsoRun(id)}/report`)).text();
export const metriche = async (id: string) => (await chiama(`${percorsoRun(id)}/metrics`)).blob();
export const configRun = (id: string) => json<ConfigValidazione>(chiama(`${percorsoRun(id)}/config`));
export const annulla = async (id: string) => {
  await chiama(`${percorsoRun(id)}/cancel`, { method: "POST" });
};
export const riprendi = (id: string, chiave: string | null) =>
  json<{ id: string }>(chiama(`${percorsoRun(id)}/resume`, corpo({ key: chiave || null })));
