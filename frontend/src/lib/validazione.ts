// Validazione dalla UI: conversione tra i profili di validation/config.yaml e le opzioni del modulo di
// caricamento (Options, riusate da OpzioniRichiesta), filtri del dataset.
import type { Mode, Options, VlmPhase } from "../api/tipi";
import type { ConfigValidazione, DocumentoDataset, Parametri, Profilo, Soglie } from "../api/validazione";
import { FASI_VLM } from "./nomi";

export const TIPI: Record<string, string> = {
  referto_laboratorio: "Referti di laboratorio",
  test_genetico: "Test genetici",
  biopsia_patologica: "Biopsie patologiche",
  lettera_dimissione: "Lettere di dimissione / TI",
  nota_ambulatoriale: "Note ambulatoriali",
  autorizzazione_preventiva: "Autorizzazioni preventive",
  referto_radiologico: "Referti radiologici",
  referto_esterno: "Referti esterni",
};
export const DIFFICOLTA = ["facile", "media", "complessa"] as const;
export const NOME_PROFILO = /^[a-z0-9][a-z0-9_-]{0,40}$/;

/** Un profilo nel modulo: nome, descrizione e le opzioni del modulo di caricamento. */
export interface ProfiloUI {
  chiave: number; // identita' React, non inviata
  nome: string;
  descrizione: string;
  o: Options;
}

export interface Filtri {
  tipi: string[];
  difficolta: string[];
  documenti: string[];
}

const modelloFase = (f: VlmPhase) => `model_${f}` as const;

/** Parametri effettivi (comune + profilo) -> opzioni del modulo. Il modo assente diventa quello del
 *  servizio: il modulo ne mostra sempre uno. */
export function daParametri(p: Parametri, comune: Parametri, modoServizio: Mode): Options {
  const e: Parametri = { ...comune };
  for (const [k, v] of Object.entries(p)) if (v !== null && v !== undefined) (e as Record<string, unknown>)[k] = v;
  const models: Options["models"] = {};
  for (const f of FASI_VLM) if (e[modelloFase(f)]) models[f] = e[modelloFase(f)] as string;
  return {
    models,
    mode: e.mode ?? modoServizio,
    verification: e.verification ?? "",
    extraction: e.extraction ?? "",
    enable: e.enable ?? [],
    disable: e.disable ?? [],
    transcript: true,
    layout: e.layout ?? false,
    detectors: e.detectors ?? null,
  };
}

/** Opzioni del modulo -> profilo completo (campo vuoto = default del servizio). La trascrizione non e'
 *  un parametro: la validazione la chiede sempre. */
export function aProfilo(p: ProfiloUI): Profilo {
  const o = p.o;
  const out: Profilo = {
    nome: p.nome.trim(),
    descrizione: p.descrizione.trim(),
    mode: o.mode,
    verification: o.verification || null,
    extraction: o.extraction || null,
    enable: o.enable.length ? o.enable : null,
    disable: o.disable.length ? o.disable : null,
    detectors: o.detectors,
    layout: o.layout,
  };
  for (const f of FASI_VLM) out[modelloFase(f)] = o.models[f] || null;
  return out;
}

/** Profili del file di configurazione per il modulo (comune fuso in ogni profilo). */
export function profiliUI(c: ConfigValidazione, modoServizio: Mode, primaChiave = 1): ProfiloUI[] {
  return c.profili.map((p, i) => ({
    chiave: primaChiave + i,
    nome: p.nome,
    descrizione: p.descrizione ?? "",
    o: daParametri(p, c.comune, modoServizio),
  }));
}

/** Configurazione da inviare: i profili sono completi, quindi `comune` e' vuoto. */
export function daInviare(
  base: ConfigValidazione,
  profili: ProfiloUI[],
  filtri: Filtri,
  soglie: Soglie,
  esecuzione: { concorrenza: number; timeout_documento_s: number },
): ConfigValidazione {
  return {
    ...base,
    server: { ...base.server, ...esecuzione },
    dataset: { ...base.dataset, ...filtri },
    soglie,
    comune: {},
    profili: profili.map(aProfilo),
  };
}

/** Documenti selezionati dai filtri (vuoto = tutti), come load_dataset del backend. */
export function selezionati(docs: DocumentoDataset[], f: Filtri): DocumentoDataset[] {
  return docs.filter(
    (d) =>
      (!f.tipi.length || f.tipi.includes(d.type)) &&
      (!f.difficolta.length || f.difficolta.includes(d.difficulty)) &&
      (!f.documenti.length || f.documenti.includes(d.id)),
  );
}

/** Problemi che impediscono l'avvio, in italiano (il backend li ricontrolla comunque). */
export function problemi(profili: ProfiloUI[], documenti: number, chiaveMancante: boolean): string[] {
  const out: string[] = [];
  if (!profili.length) out.push("serve almeno un profilo");
  if (!documenti) out.push("nessun documento corrisponde ai filtri");
  const nomi = profili.map((p) => p.nome.trim());
  nomi.forEach((n, i) => {
    if (!NOME_PROFILO.test(n)) out.push(`profilo ${i + 1}: nome non valido (minuscole, cifre, - e _)`);
  });
  if (new Set(nomi).size !== nomi.length) out.push("i nomi dei profili devono essere diversi");
  for (const p of profili) {
    if (p.o.detectors && !p.o.detectors.some((r) => r !== "eponyms")) {
      out.push(`${p.nome}: serve almeno un rilevatore oltre al filtro eponimi`);
    }
  }
  if (chiaveMancante) out.push("modalita' pseudonimo: serve la chiave");
  return out;
}

/** Nome libero per un nuovo profilo (profilo-2, profilo-3...). */
export function nuovoNome(profili: ProfiloUI[], base = "profilo"): string {
  const nomi = new Set(profili.map((p) => p.nome));
  let n = profili.length + 1;
  while (nomi.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}
