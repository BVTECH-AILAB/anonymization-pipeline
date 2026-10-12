// Categorie scelte per richiesta (campi enable/disable di POST /jobs): gruppi interi o categorie singole,
// come in Registry.expand del backend. Le modifiche sono sempre la differenza dalle attive del servizio.
import type { Categories, Options } from "../api/tipi";

export type Modifiche = Pick<Options, "enable" | "disable">;
export type StatoGruppo = "tutte" | "alcune" | "nessuna";

/** Nomi di gruppi o di categorie -> categorie (i nomi sconosciuti sono ignorati). */
export function espandi(cat: Categories, voci: string[]): Set<string> {
  const out = new Set<string>();
  for (const v of voci) {
    const gruppo = cat.groups[v.toLowerCase()];
    if (gruppo) gruppo.forEach((c) => out.add(c));
    else if (v.toUpperCase() in cat.descriptions) out.add(v.toUpperCase());
  }
  return out;
}

/** Categorie attive con le modifiche della richiesta (disable vince, come nel backend). */
export function scelte(cat: Categories, m: Modifiche): Set<string> {
  const out = new Set([...cat.active, ...espandi(cat, m.enable)]);
  espandi(cat, m.disable).forEach((c) => out.delete(c));
  return out;
}

/** Differenza tra le scelte e le attive del servizio; un gruppo intero diventa il nome del gruppo. */
export function modifiche(cat: Categories, scelte: Set<string>): Modifiche {
  const enable: string[] = [];
  const disable: string[] = [];
  for (const [g, cs] of Object.entries(cat.groups)) {
    const accese = cs.filter((c) => scelte.has(c) && !cat.active.includes(c));
    const spente = cs.filter((c) => !scelte.has(c) && cat.active.includes(c));
    enable.push(...(accese.length === cs.length ? [g] : accese));
    disable.push(...(spente.length === cs.length ? [g] : spente));
  }
  return { enable, disable };
}

export function statoGruppo(cat: Categories, scelte: Set<string>, gruppo: string): StatoGruppo {
  const n = cat.groups[gruppo].filter((c) => scelte.has(c)).length;
  return n === 0 ? "nessuna" : n === cat.groups[gruppo].length ? "tutte" : "alcune";
}

export function alternaCategoria(cat: Categories, m: Modifiche, categoria: string): Modifiche {
  const s = scelte(cat, m);
  if (s.has(categoria)) s.delete(categoria);
  else s.add(categoria);
  return modifiche(cat, s);
}

/** Tutte accese, oppure tutte spente se lo erano gia' tutte. */
export function alternaGruppo(cat: Categories, m: Modifiche, gruppo: string): Modifiche {
  const s = scelte(cat, m);
  const accendi = statoGruppo(cat, s, gruppo) !== "tutte";
  cat.groups[gruppo].forEach((c) => (accendi ? s.add(c) : s.delete(c)));
  return modifiche(cat, s);
}

/** Numero di categorie che la richiesta cambia rispetto alle attive del servizio. */
export function numeroModifiche(cat: Categories, m: Modifiche): number {
  const s = scelte(cat, m);
  return Object.values(cat.groups).flat().filter((c) => s.has(c) !== cat.active.includes(c)).length;
}
