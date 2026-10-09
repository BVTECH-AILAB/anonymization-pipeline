// Evidenziazione di intervalli (tag nel censurato, valori originali nel testo in chiaro) sia nella vista
// grezza sia nel Markdown reso: gli intervalli vengono marcati con caratteri dell'area privata Unicode
// prima del rendering e trasformati in <mark> da un plugin rehype.

export interface Intervallo {
  inizio: number;
  fine: number;
  chiave: string; // raggruppa le occorrenze (es. il sostituto "[PERSONA_1]")
  categoria: string | null;
  classe?: string; // classe CSS in piu' (es. "sfuggito" per le segnalazioni della revisione)
}

export interface Segmento {
  testo: string;
  inizio: number; // offset nel testo intero
  intervallo?: Intervallo;
}

/** Testo [da, a) diviso in segmenti semplici ed evidenziati. Intervalli sovrapposti: vince il primo. */
export function segmenti(testo: string, intervalli: Intervallo[], da = 0, a = testo.length): Segmento[] {
  const out: Segmento[] = [];
  let cursore = da;
  const ordinati = intervalli.filter((x) => x.fine > da && x.inizio < a).sort((x, y) => x.inizio - y.inizio);
  for (const x of ordinati) {
    const i = Math.max(x.inizio, da);
    const f = Math.min(x.fine, a);
    if (i < cursore) continue;
    if (i > cursore) out.push({ testo: testo.slice(cursore, i), inizio: cursore });
    out.push({ testo: testo.slice(i, f), inizio: i, intervallo: x });
    cursore = f;
  }
  if (cursore < a) out.push({ testo: testo.slice(cursore, a), inizio: cursore });
  return out;
}

// --- marcatori per il Markdown reso ------------------------------------------------------------
const APRI = "";
const SEP = "";
const CHIUDI = "";

/** Testo con ogni intervallo racchiuso in APRI indice SEP ... CHIUDI (indice nell'array restituito). */
export function marca(testo: string, intervalli: Intervallo[], da = 0, a = testo.length): [string, Intervallo[]] {
  const usati: Intervallo[] = [];
  const parti = segmenti(testo, intervalli, da, a).map((s) => {
    const t = s.testo.replace(/[-]/g, "");
    if (!s.intervallo) return t;
    usati.push(s.intervallo);
    return `${APRI}${usati.length - 1}${SEP}${t}${CHIUDI}`;
  });
  return [parti.join(""), usati];
}

type Nodo = { type: string; value?: string; tagName?: string; properties?: Record<string, unknown>; children?: Nodo[] };

/** Plugin rehype: i marcatori diventano <mark data-i="indice">. Un intervallo puo' attraversare piu'
 *  nodi di testo (enfasi dentro un valore): ogni pezzo viene racchiuso nel suo <mark>. */
export function rehypeMarcatori() {
  return (albero: Nodo) => {
    let aperto: number | null = null;
    const visita = (n: Nodo) => {
      if (!n.children) return;
      const nuovi: Nodo[] = [];
      for (const c of n.children) {
        if (c.type !== "text" || !c.value) {
          visita(c);
          nuovi.push(c);
          continue;
        }
        let resto = c.value;
        while (resto.length) {
          if (aperto === null) {
            const i = resto.indexOf(APRI);
            if (i < 0) {
              nuovi.push({ type: "text", value: resto });
              break;
            }
            if (i > 0) nuovi.push({ type: "text", value: resto.slice(0, i) });
            const s = resto.indexOf(SEP, i);
            aperto = Number(resto.slice(i + 1, s));
            resto = resto.slice(s + 1);
          } else {
            const f = resto.indexOf(CHIUDI);
            const pezzo = f < 0 ? resto : resto.slice(0, f);
            if (pezzo) nuovi.push(mark(aperto, pezzo));
            if (f < 0) break;
            aperto = null;
            resto = resto.slice(f + 1);
          }
        }
      }
      n.children = nuovi;
    };
    visita(albero);
  };
}

function mark(i: number, testo: string): Nodo {
  return { type: "element", tagName: "mark", properties: { dataI: String(i) }, children: [{ type: "text", value: testo }] };
}

/** Colore (tonalita') stabile per categoria. */
export function colore(categoria: string | null): string {
  if (!categoria) return "hsl(0 0% 60%)";
  let h = 0;
  for (const ch of categoria) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 45%)`;
}
