// Divisione del Markdown del documento nelle sue pagine, con gli offset nel testo intero.
// Le pagine sono separate da output.page_separator del servizio ("\n\n<!-- pagina N -->\n\n").

export interface Pagina {
  numero: number;
  inizio: number; // offset nel testo intero
  fine: number;
}

const SEPARATORE = /\n*<!-- pagina (\d+) -->\n*/g;

export function dividiPagine(testo: string): Pagina[] {
  const pagine: Pagina[] = [];
  let inizio = 0;
  let numero = 1;
  for (const m of testo.matchAll(SEPARATORE)) {
    pagine.push({ numero, inizio, fine: m.index });
    numero = Number(m[1]);
    inizio = m.index + m[0].length;
  }
  pagine.push({ numero, inizio, fine: testo.length });
  return pagine;
}

/** Numero della pagina che contiene l'offset. */
export function paginaDi(pagine: Pagina[], offset: number): number {
  for (const p of pagine) if (offset < p.fine || p === pagine[pagine.length - 1]) return p.numero;
  return 1;
}
