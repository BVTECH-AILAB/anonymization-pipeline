import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Categories, Models, Options, Phase, Status } from "../api/tipi";
import OpzioniRichiesta from "../componenti/OpzioniRichiesta";
import { FASI_VLM, nomeFase, nomeModalita, nomeMotivoRifiuto } from "../lib/nomi";
import { RILEVATORI, senzaRilevatori } from "../lib/rilevatori";
import { useSessione, type Documento } from "../stato/sessione";

const FASI: Phase[] = ["ingest", "extraction", "detection", "redaction", "verification"];
const ESTENSIONI = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt,.md,.csv,.json,.xml,.html,.eml";

const PREDEFINITE: Options = { models: {}, mode: "pseudonym", verification: "", extraction: "", enable: [], disable: [], transcript: false, layout: false, detectors: null };


/** "rilevamento <modello>" per le fasi con un modello scelto. */
const modelliScelti = (o: Options) => FASI_VLM.filter((f) => o.models[f]).map((f) => `${nomeFase(f)} ${o.models[f]}`);

function Avanzamento({ d }: { d: Documento }) {
  const l = d.lavoro;
  if (l.status === "queued") return <span className="tenue">in coda{l.position ? ` (posizione ${l.position})` : ""}</span>;
  if (l.status === "rejected") {
    const residui = (l.residuals ?? []).map((r) => `${r.category} p.${r.page}`).join(", ");
    return (
      <span className="errore">
        rifiutato{l.reason ? ` (${nomeMotivoRifiuto(l.reason)})` : ""}{residui ? `: ${residui}` : ""}
      </span>
    );
  }
  if (l.status === "error") return <span className="errore">errore: {l.error}</span>;
  if (l.status === "cancelled") return <span className="tenue">annullato o scaduto</span>;
  const corrente = l.status === "completed" ? FASI.length : Math.max(0, FASI.indexOf(l.phase as Phase));
  const fase = l.phase ? nomeFase(l.phase) : "avvio";
  return (
    <div>
      <div className="passi" aria-label={`fase ${fase}`}>
        {FASI.map((f, i) => (
          <div key={f} title={nomeFase(f)} className={`passo ${i < corrente ? "fatto" : i === corrente ? "corrente" : ""}`} />
        ))}
      </div>
      <span className="nota">
        {l.status === "completed" ? "completato" : fase}
        {" · "}chiamate VLM {l.vlm_calls}
      </span>
    </div>
  );
}

function Durata({ d }: { d: Documento }) {
  const [, setT] = useState(0);
  const l = d.lavoro;
  useEffect(() => {
    if (l.status !== "running") return;
    const t = setInterval(() => setT((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [l.status]);
  if (!l.started) return <>–</>;
  const s = (l.finished ?? Date.now() / 1000) - l.started;
  return <>{s.toFixed(0)} s</>;
}

function scaricaPdf(d: Documento) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(api.base64InBlob(d.risultato!.layout_pdf_b64!));
  a.download = `${d.nome.replace(/\.[^.]+$/, "")}.anon.pdf`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function scaricaZip(d: Documento) {
  const blob = await api.risultatoZip(d.id);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${d.nome.replace(/\.[^.]+$/, "")}.anonimizzato.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function Carica() {
  const s = useSessione();
  const [o, setO] = useState<Options>(PREDEFINITE);
  const [file, setFile] = useState<File[]>([]);
  const [sopra, setSopra] = useState(false);
  const [cat, setCat] = useState<Categories | null>(null);
  const [mod, setMod] = useState<Models | null>(null);
  const [stato, setStato] = useState<Status | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [invio, setInvio] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { api.categorie().then(setCat).catch(() => setCat(null)); }, []);
  const leggiModelli = () => api.modelli().then(setMod).catch(() => setMod(null));
  useEffect(() => { leggiModelli(); }, []);
  const leggiStato = () => api.salute().then(setStato).catch(() => setStato(null));
  useEffect(() => { leggiStato(); }, []);

  const senza_rilevatori = senzaRilevatori(o.detectors);

  const manca_chiave = o.mode === "pseudonym" && !s.chiave;
  const avvia = async () => {
    setErrore(null);
    setInvio(true);
    try {
      await s.carica(file, o);
      setFile([]);
    } catch (e) {
      setErrore((e as Error).message);
    } finally {
      setInvio(false);
    }
  };
  return (
    <section>
      <h1>Carica documenti</h1>
      <div className="griglia-2">
        <div className="scheda">
          <div
            className={`zona-rilascio ${sopra ? "sopra" : ""}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setSopra(true); }}
            onDragLeave={() => setSopra(false)}
            onDrop={(e) => { e.preventDefault(); setSopra(false); const scelti = Array.from(e.dataTransfer.files); setFile((f) => [...f, ...scelti]); }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
          >
            Trascina qui PDF, immagini o file di testo, oppure fai clic per sceglierli
            <input ref={input} type="file" multiple accept={ESTENSIONI} hidden
                   onChange={(e) => { const scelti = Array.from(e.target.files ?? []); setFile((f) => [...f, ...scelti]); e.target.value = ""; }} />
          </div>
          {file.length > 0 && (
            <ul>
              {file.map((f, i) => (
                <li key={i}>
                  {f.name} <span className="tenue">({(f.size / 1024).toFixed(0)} KB)</span>{" "}
                  <button className="secondario piccolo" onClick={() => setFile((x) => x.filter((_, j) => j !== i))}>×</button>
                </li>
              ))}
            </ul>
          )}
          <div className="riga" style={{ marginTop: 10 }}>
            <button disabled={!file.length || invio || manca_chiave || senza_rilevatori} onClick={avvia}>
              Anonimizza {file.length > 1 ? `${file.length} documenti` : ""}
            </button>
            {manca_chiave && (
              <span className="nota">
                La modalità pseudonimo richiede la chiave della mappa:{" "}
                <button className="secondario piccolo" onClick={() => s.impostaChiave(api.generaChiave())}>genera</button>{" "}
                (poi conservala dal pannello Sessione) oppure scegli la modalità tag.
              </span>
            )}
          </div>
          {senza_rilevatori && <p className="nota errore">Scegli almeno un rilevatore: il filtro eponimi da solo non trova valori.</p>}
          {errore && <p className="errore">{errore}</p>}
        </div>

        <OpzioniRichiesta o={o} setO={setO} predefinite={PREDEFINITE} cat={cat} mod={mod} stato={stato}
                          leggiModelli={leggiModelli} riprovaStato={leggiStato} />
      </div>

      <h2>Lavori di questa sessione</h2>
      {s.documenti.length === 0 ? (
        <p className="tenue">Nessun documento caricato.</p>
      ) : (
        <table className="tabella">
          <thead>
            <tr><th>Documento</th><th>Modalità</th><th style={{ width: "34%" }}>Avanzamento</th><th className="num">Durata</th><th>Azioni</th></tr>
          </thead>
          <tbody>
            {s.documenti.map((d) => (
              <tr key={d.id}>
                <td>{d.nome}{d.errore && <div className="nota errore">{d.errore}</div>}</td>
                <td>{nomeModalita(d.opzioni.mode)}{d.opzioni.transcript ? " + trascrizione" : ""}{d.opzioni.layout ? " + PDF" : ""}{modelliScelti(d.opzioni).length > 0 && <div className="nota">{modelliScelti(d.opzioni).join(" · ")}</div>}
                  {d.opzioni.detectors && <div className="nota">rilevatori: {d.opzioni.detectors.map((r) => RILEVATORI.find((x) => x.r === r)?.nome ?? r).join(", ")}</div>}
                  {(d.opzioni.enable.length > 0 || d.opzioni.disable.length > 0) && (
                    <div className="nota">categorie: {[...d.opzioni.enable.map((x) => `+${x}`), ...d.opzioni.disable.map((x) => `-${x}`)].join(" ")}</div>
                  )}</td>
                <td><Avanzamento d={d} /></td>
                <td className="num"><Durata d={d} /></td>
                <td>
                  <div className="riga">
                    {d.risultato && <Link to={`/confronto/${d.id}`}>Confronto</Link>}
                    {d.risultato && <Link to={`/dashboard/${d.id}`}>Dashboard</Link>}
                    {d.risultato && <button className="secondario piccolo" onClick={() => scaricaZip(d)}>ZIP</button>}
                    {d.risultato?.layout_pdf_b64 && <button className="secondario piccolo" onClick={() => scaricaPdf(d)}>PDF</button>}
                    <button className="secondario piccolo" onClick={() => s.rimuovi(d.id)}>Rimuovi</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
