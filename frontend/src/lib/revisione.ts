// Revisione dell'operatore: segnalazioni su un documento censurato. Il file esportato contiene solo
// posizioni nel Markdown censurato, lunghezze, categorie, tipo e note: nessun valore del documento.
import type { Pagina } from "./pagine";
import { paginaDi } from "./pagine";

export type TipoVoce = "sfuggito" | "censura_errata" | "categoria_errata";

export interface VoceRevisione {
  tipo: TipoVoce;
  inizio: number; // nel Markdown censurato
  fine: number;
  categoria: string | null; // sfuggito: categoria proposta; altri: categoria del tag
  categoria_corretta?: string | null;
  sostituto?: string; // il tag, per censura_errata / categoria_errata (non e' un valore)
  nota?: string;
}

export interface FileRevisione {
  versione: 1;
  documento: { sha256: string; pagine: number };
  modello: string;
  creato: string;
  voci: (Omit<VoceRevisione, "fine"> & { fine: number; lunghezza: number; pagina: number })[];
}

export function esportaRevisione(voci: VoceRevisione[], sha256: string, modello: string, pagine: Pagina[]): FileRevisione {
  return {
    versione: 1,
    documento: { sha256, pagine: pagine.length },
    modello,
    creato: new Date().toISOString(),
    voci: [...voci]
      .sort((a, b) => a.inizio - b.inizio)
      .map((v) => ({
        tipo: v.tipo,
        inizio: v.inizio,
        fine: v.fine,
        lunghezza: v.fine - v.inizio,
        pagina: paginaDi(pagine, v.inizio),
        categoria: v.categoria,
        ...(v.categoria_corretta ? { categoria_corretta: v.categoria_corretta } : {}),
        ...(v.sostituto ? { sostituto: v.sostituto } : {}),
        ...(v.nota ? { nota: v.nota } : {}),
      })),
  };
}
