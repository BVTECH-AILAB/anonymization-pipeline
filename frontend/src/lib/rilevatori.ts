// Rilevatori e filtro eponimi scelti per richiesta (campo detectors di POST /jobs).
import type { Rilevatore, Status } from "../api/tipi";

export const RILEVATORI: { r: Rilevatore; nome: string; nota: string }[] = [
  { r: "vlm", nome: "rilevamento VLM", nota: "il modello legge testo e immagine della pagina" },
  { r: "rules", nome: "regole", nota: "regex e checksum: codice fiscale, IBAN, partita IVA, telefoni..." },
  { r: "labels", nome: "etichette", nota: "il valore dopo un'etichetta (\"Paziente: ...\")" },
  { r: "ner", nome: "NER", nota: "modello di riconoscimento delle entità, su CPU" },
  { r: "eponyms", nome: "filtro eponimi", nota: "scarta Parkinson, Valsalva, \"globuli rossi\"...: non trova valori" },
];

/** Rilevatori accesi nella configurazione del servizio (GET /readyz); null se lo stato non e' noto. */
export function predefiniti(s: Status | null): Rilevatore[] | null {
  if (!s) return null;
  const d = s.detectors;
  return RILEVATORI.map((x) => x.r).filter((r) => (r === "ner" ? d.ner.enabled : d[r]));
}

const stessi = (a: Rilevatore[], b: Rilevatore[]) => a.length === b.length && a.every((r) => b.includes(r));

/** Nuova scelta dopo il clic su ``r``. Con i predefiniti sconosciuti non cambia nulla: partire da un
 *  insieme vuoto spegnerebbe tutti gli altri rilevatori. Tornati ai predefiniti: null (nessun campo). */
export function alterna(scelti: Rilevatore[] | null, base: Rilevatore[] | null, r: Rilevatore): Rilevatore[] | null {
  if (!base) return scelti;
  const da = scelti ?? base;
  const nuovi = da.includes(r) ? da.filter((y) => y !== r) : [...da, r];
  return stessi(nuovi, base) ? null : nuovi;
}

/** Nessun rilevatore che trovi valori (il filtro eponimi da solo ne scarta soltanto). */
export const senzaRilevatori = (scelti: Rilevatore[] | null) => scelti !== null && !scelti.some((r) => r !== "eponyms");
