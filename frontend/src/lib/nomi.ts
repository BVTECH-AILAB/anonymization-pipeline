// Nomi italiani, per l'interfaccia, dei valori dell'API (in inglese). Un valore sconosciuto (es. di una
// versione piu' recente del servizio) resta com'e'.

import type { Report, VlmPhase } from "../api/tipi";

const FASI: Record<string, string> = {
  ingest: "acquisizione",
  extraction: "estrazione",
  detection: "rilevamento",
  redaction: "censura",
  verification: "verifica",
  completed: "completato",
  slot_wait: "attesa in coda",
};
const MODALITA: Record<string, string> = { pseudonym: "pseudonimo", tag: "tag", redaction: "redazione" };
const VERIFICHE: Record<string, string> = { off: "off", report: "segnala", block: "blocca" };
const ESITI: Record<string, string> = { off: "off", ok: "ok", skipped: "non eseguita", residuals: "residui" };
const MOTIVI_RIFIUTO: Record<string, string> = {
  residuals: "residui",
  invariant: "invariante",
  verification_unavailable: "verifica non disponibile",
};
const MOTIVI_VLM: Record<string, string> = {
  rotated: "ruotata",
  little_text: "poco testo",
  unreadable_text: "testo illeggibile",
  images: "immagini",
  hidden_text: "testo nascosto",
  vlm_engine: "motore VLM",
  docling_unavailable: "docling non disponibile",
};
// rilevatori (metriche, report.detection.sources) e fonti delle entita' ("detection" e' il VLM)
const FONTI: Record<string, string> = {
  rules: "regole",
  labels: "etichette",
  eponyms: "eponimi",
  ner: "ner",
  vlm: "vlm",
  detection: "rilevamento",
  verification: "verifica",
};
const AVVISI: Record<string, string> = {
  extraction_truncated: "estrazione troncata",
  extraction_empty: "estrazione vuota",
  docling_unavailable: "docling non disponibile",
  verification_skipped: "verifica non eseguita",
  residuals_not_fixed: "residui non corretti",
  layout_unavailable: "PDF con layout non prodotto",
};

const nome = (mappa: Record<string, string>, v: string) => (Object.hasOwn(mappa, v) ? mappa[v] : v);

/** Fase del lavoro, chiave di times_s o fase delle chiamate al VLM. */
export const nomeFase = (v: string) => nome(FASI, v);
export const nomeModalita = (v: string) => nome(MODALITA, v);
export const nomeVerifica = (v: string) => nome(VERIFICHE, v);
export const nomeEsito = (v: string) => nome(ESITI, v);
export const nomeMotivoRifiuto = (v: string) => nome(MOTIVI_RIFIUTO, v);
export const nomeMotivoVlm = (v: string) => nome(MOTIVI_VLM, v);
export const nomeFonte = (v: string) => nome(FONTI, v);

export const FASI_VLM: VlmPhase[] = ["extraction", "detection", "verification"];

/** Modelli di un report: i nomi distinti in ordine di fase ("a + b"). */
export const etichettaModelli = (m: Report["model"]) => [...new Set(FASI_VLM.map((f) => m.phases[f]))].join(" + ");

/** "extraction_truncated:page_3" -> "estrazione troncata (pagina 3)", "ner_unavailable" -> "ner non disponibile". */
export function testoAvviso(a: string): string {
  const i = a.indexOf(":");
  const codice = i < 0 ? a : a.slice(0, i);
  const dove = i < 0 ? "" : a.slice(i + 1);
  const testo = Object.hasOwn(AVVISI, codice)
    ? AVVISI[codice]
    : codice.endsWith("_unavailable")
      ? `${nomeFonte(codice.slice(0, -"_unavailable".length))} non disponibile`
      : null;
  if (testo === null) return a;
  const pagina = /^page_(\d+)$/.exec(dove)?.[1];
  return pagina ? `${testo} (pagina ${pagina})` : dove ? `${testo} (${dove})` : testo;
}
