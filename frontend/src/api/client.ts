// Client dell'API: tutte le chiamate passano da /api (proxy di nginx o di Vite verso l'API).
import type { ApiError, Categories, Job, Models, Options, RestoreDetail, Result, Status } from "./tipi";

export const BASE = "/api";

export class ErroreApi extends Error {
  constructor(public stato: number, messaggio: string, public corpo: ApiError | null = null) {
    super(messaggio);
  }
}

/** Errore di una risposta non riuscita: il messaggio e' il `detail` del corpo JSON dell'API. */
async function errore(r: Response): Promise<ErroreApi> {
  let corpo: ApiError | null = null;
  try {
    const j: unknown = await r.json();
    if (j && typeof j === "object" && typeof (j as ApiError).detail === "string") corpo = j as ApiError;
  } catch {
    /* corpo non JSON (es. pagina di errore del proxy) */
  }
  return new ErroreApi(r.status, corpo?.detail ?? `${r.status} ${r.statusText}`, corpo);
}

async function chiama(percorso: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(BASE + percorso, init);
  if (!r.ok) throw await errore(r);
  return r;
}

const json = async <T>(p: Promise<Response>): Promise<T> => (await p).json() as Promise<T>;

const eStato = (x: unknown): x is Status =>
  !!x && typeof x === "object" && typeof (x as Status).status === "string" && !!(x as Status).backend;

/** Prontezza e componenti del servizio. /readyz risponde 503 se il servizio non e' pronto, con lo stesso
 *  corpo: non e' un errore dell'API, lo stato si mostra comunque. */
export async function salute(): Promise<Status> {
  const r = await fetch(BASE + "/readyz");
  if (r.ok) return r.json() as Promise<Status>;
  if (r.status === 503) {
    const corpo: unknown = await r.clone().json().catch(() => null);
    if (eStato(corpo)) return corpo;
  }
  throw await errore(r);
}

export const categorie = () => json<Categories>(chiama("/categories"));
export const modelli = () => json<Models>(chiama("/models"));

export function nuovoLavoro(file: File, o: Options, chiave: string | null): Promise<Job> {
  const f = new FormData();
  f.append("file", file, file.name);
  f.append("mode", o.mode);
  if (o.model) f.append("model", o.model);
  if (o.verification) f.append("verification", o.verification);
  if (o.extraction) f.append("extraction", o.extraction);
  if (o.enable.length) f.append("enable", o.enable.join(","));
  if (o.disable.length) f.append("disable", o.disable.join(","));
  if (o.transcript) f.append("transcript", "true");
  if (chiave && o.mode === "pseudonym") f.append("key", chiave);
  return json<Job>(chiama("/jobs", { method: "POST", body: f }));
}

const percorsoLavoro = (id: string) => `/jobs/${encodeURIComponent(id)}`;

export const lavoro = (id: string) => json<Job>(chiama(percorsoLavoro(id)));
export const risultato = (id: string) => json<Result>(chiama(`${percorsoLavoro(id)}/result`));
export const risultatoZip = async (id: string) => (await chiama(`${percorsoLavoro(id)}/result?format=zip`)).blob();
export const cancellaLavoro = async (id: string) => {
  await chiama(percorsoLavoro(id), { method: "DELETE" });
};

function moduloRipristino(markdown: string, mappa: Blob, chiave: string): FormData {
  const f = new FormData();
  f.append("markdown", new Blob([markdown], { type: "text/markdown" }), "documento.anon.md");
  f.append("map", mappa, "documento.mappa.enc");
  f.append("key", chiave);
  return f;
}

export const ripristinaDettaglio = (markdown: string, mappa: Blob, chiave: string) =>
  json<RestoreDetail>(chiama("/restore/detail", { method: "POST", body: moduloRipristino(markdown, mappa, chiave) }));

export function base64InBlob(b64: string): Blob {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return new Blob([out]);
}

/** Chiave casuale AES-256 in base64 url-safe, nel formato delle chiavi generate dal servizio. */
export function generaChiave(): string {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_");
}
