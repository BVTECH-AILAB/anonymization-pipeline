// Stato della sessione: chiave della mappa (sessionStorage, mai localStorage), documenti caricati con il loro
// lavoro, il file originale (solo in memoria) e il risultato. Il polling aggiorna i lavori non conclusi; a
// lavoro completato scarica il risultato e salva report e metriche nello storico del browser.
// Gli id dei lavori (e le opzioni) restano nella scheda: dopo un ricaricamento i lavori si recuperano
// dall'API finche' non scadono; il file originale no (era solo in memoria).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as api from "../api/client";
import type { Job, JobStatus, Options, RestoreDetail, Result } from "../api/tipi";
import type { VoceRevisione } from "../lib/revisione";
import * as storico from "../lib/storico";

export interface Documento {
  id: string;
  nome: string;
  file?: File; // assente dopo un ricaricamento della pagina
  opzioni: Options;
  lavoro: Job;
  risultato?: Result;
  chiaro?: RestoreDetail | null; // testo in chiaro con le posizioni (ripristino o trascrizione)
  errore?: string;
  revisione: VoceRevisione[];
}

interface Sessione {
  chiave: string;
  impostaChiave: (k: string) => void;
  documenti: Documento[];
  carica: (files: File[], o: Options) => Promise<string[]>;
  aggiorna: (id: string, f: (d: Documento) => Partial<Documento>) => void;
  rimuovi: (id: string) => Promise<void>;
  svuota: () => Promise<void>;
  testoInChiaro: (id: string) => Promise<RestoreDetail | null>;
}

const Ctx = createContext<Sessione | null>(null);

function leggi(k: string): string {
  try {
    return sessionStorage.getItem(k) ?? "";
  } catch {
    return "";
  }
}

function scrivi(k: string, v: string) {
  try {
    if (v) sessionStorage.setItem(k, v);
    else sessionStorage.removeItem(k);
  } catch {
    /* storage non disponibile: resta in memoria */
  }
}

// chiavi di sessionStorage (prefisso anonpip): quelle della versione precedente non si leggono
const CHIAVE = "anonpip.chiave";
const LAVORI = "anonpip.lavori";

const FINITI = new Set<JobStatus>(["completed", "rejected", "error", "cancelled"]);

export function ProviderSessione({ children }: { children: ReactNode }) {
  const [chiave, setChiave] = useState(() => leggi(CHIAVE));
  const [documenti, setDocumenti] = useState<Documento[]>([]);
  const ref = useRef(documenti);
  ref.current = documenti;

  // lavori della scheda: id e opzioni in sessionStorage, il resto si rilegge dall'API
  const primo = useRef(true);
  useEffect(() => {
    if (primo.current) { primo.current = false; return; }     // non cancellare l'elenco prima di averlo riletto
    scrivi(LAVORI, documenti.length ? JSON.stringify(documenti.map((d) => ({ id: d.id, opzioni: d.opzioni }))) : "");
  }, [documenti]);
  const [salvati] = useState<{ id: string; opzioni: Options }[]>(() => {
    try { return JSON.parse(leggi(LAVORI) || "[]"); } catch { return []; }
  });
  useEffect(() => {
    (async () => {
      for (const { id, opzioni } of salvati) {
        try {
          const lavoro = await api.lavoro(id);
          const risultato = lavoro.status === "completed" ? await api.risultato(id) : undefined;
          setDocumenti((ds) => ds.some((d) => d.id === id) ? ds : [...ds, { id, nome: lavoro.name, opzioni, lavoro, risultato, revisione: [] }]);
        } catch {
          /* scaduto o cancellato */
        }
      }
    })();
  }, [salvati]);

  const impostaChiave = useCallback((k: string) => {
    setChiave(k);
    scrivi(CHIAVE, k);
  }, []);

  const aggiorna = useCallback((id: string, f: (d: Documento) => Partial<Documento>) => {
    setDocumenti((ds) => ds.map((d) => (d.id === id ? { ...d, ...f(d) } : d)));
  }, []);

  const carica = useCallback(
    async (files: File[], o: Options) => {
      const ids: string[] = [];
      for (const file of files) {
        const lavoro = await api.nuovoLavoro(file, o, chiave);
        ids.push(lavoro.id);
        setDocumenti((ds) => [...ds, { id: lavoro.id, nome: file.name, file, opzioni: o, lavoro, revisione: [] }]);
      }
      return ids;
    },
    [chiave],
  );

  // polling dei lavori non conclusi
  useEffect(() => {
    const t = setInterval(async () => {
      for (const d of ref.current) {
        if (FINITI.has(d.lavoro.status)) continue;
        try {
          const lavoro = await api.lavoro(d.id);
          aggiorna(d.id, () => ({ lavoro }));
          if (lavoro.status === "completed") {
            const risultato = await api.risultato(d.id);
            aggiorna(d.id, () => ({ risultato }));
            await storico.salva(storico.daRisultato(risultato.report, risultato.metrics));
          }
        } catch (e) {
          aggiorna(d.id, () => ({ errore: (e as Error).message }));
          if (e instanceof api.ErroreApi && e.stato === 404) {
            aggiorna(d.id, (x) => ({ lavoro: { ...x.lavoro, status: "cancelled" } }));
          }
        }
      }
    }, 2000);
    return () => clearInterval(t);
  }, [aggiorna]);

  const rimuovi = useCallback(async (id: string) => {
    try {
      await api.cancellaLavoro(id);
    } catch {
      /* gia' scaduto */
    }
    setDocumenti((ds) => ds.filter((d) => d.id !== id));
  }, []);

  const svuota = useCallback(async () => {
    await Promise.all(ref.current.map((d) => api.cancellaLavoro(d.id).catch(() => undefined)));
    setDocumenti([]);
    impostaChiave("");
  }, [impostaChiave]);

  /** Testo in chiaro del documento: dalla trascrizione (se chiesta) o dal ripristino con la chiave. */
  const testoInChiaro = useCallback(
    async (id: string) => {
      const d = ref.current.find((x) => x.id === id);
      const r = d?.risultato;
      if (!d || !r) return null;
      if (d.chiaro) return d.chiaro;
      let chiaro: RestoreDetail | null = null;
      if (r.transcript !== undefined && r.occurrences) {
        chiaro = { text: r.transcript, occurrences: r.occurrences };
      } else if (r.encrypted_map_b64 && chiave) {
        chiaro = await api.ripristinaDettaglio(r.markdown, api.base64InBlob(r.encrypted_map_b64), chiave);
      }
      if (chiaro) aggiorna(id, () => ({ chiaro }));
      return chiaro;
    },
    [chiave, aggiorna],
  );

  const valore = useMemo(
    () => ({ chiave, impostaChiave, documenti, carica, aggiorna, rimuovi, svuota, testoInChiaro }),
    [chiave, impostaChiave, documenti, carica, aggiorna, rimuovi, svuota, testoInChiaro],
  );
  return <Ctx.Provider value={valore}>{children}</Ctx.Provider>;
}

export function useSessione(): Sessione {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSessione fuori da ProviderSessione");
  return s;
}
