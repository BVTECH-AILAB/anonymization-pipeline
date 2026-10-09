import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Categories, Models, Options, Phase } from "../api/tipi";
import { FASI_VLM, nomeFase, nomeModalita, nomeMotivoRifiuto } from "../lib/nomi";
import { useSessione, type Documento } from "../stato/sessione";

const FASI: Phase[] = ["ingest", "extraction", "detection", "redaction", "verification"];
const ESTENSIONI = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt,.md,.csv,.json,.xml,.html,.eml";

const PREDEFINITE: Options = { models: {}, mode: "pseudonym", verification: "", extraction: "", enable: [], disable: [], transcript: false };

/** "rilevamento gemma4:e4b-it-qat" per le fasi con un modello scelto (le opzioni salvate prima dei modelli
 *  per fase non hanno models). */
const modelliScelti = (o: Options) => FASI_VLM.filter((f) => o.models?.[f]).map((f) => `${nomeFase(f)} ${o.models[f]}`);

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
        {l.status === "completed" ? "completato" : `${fase}${l.iteration && (l.phase === "verification" || l.phase === "redaction") ? ` (iterazione ${l.iteration})` : ""}`}
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
  const [errore, setErrore] = useState<string | null>(null);
  const [invio, setInvio] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { api.categorie().then(setCat).catch(() => setCat(null)); }, []);
  const leggiModelli = () => api.modelli().then(setMod).catch(() => setMod(null));
  useEffect(() => { leggiModelli(); }, []);

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
  const alterna = (campo: "enable" | "disable", v: string) =>
    setO((x) => ({ ...x, [campo]: x[campo].includes(v) ? x[campo].filter((y) => y !== v) : [...x[campo], v] }));

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
            <button disabled={!file.length || invio || manca_chiave} onClick={avvia}>
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
          {errore && <p className="errore">{errore}</p>}
        </div>

        <div className="scheda">
          <h2 style={{ marginTop: 0 }}>Opzioni della richiesta</h2>
          <div className="griglia-opzioni">
            <label>
              Modalità
              <select value={o.mode} onChange={(e) => setO({ ...o, mode: e.target.value as Options["mode"] })}>
                <option value="pseudonym">pseudonimo (tag + mappa cifrata)</option>
                <option value="tag">tag</option>
                <option value="redaction">redazione</option>
              </select>
            </label>
            <label>
              Verifica
              <select value={o.verification} onChange={(e) => setO({ ...o, verification: e.target.value as Options["verification"] })}>
                <option value="">predefinita</option>
                <option value="fix">correggi</option>
                <option value="report">segnala</option>
                <option value="block">blocca</option>
                <option value="off">off</option>
              </select>
            </label>
            <label>
              Estrazione dei PDF nativi
              <select value={o.extraction} onChange={(e) => setO({ ...o, extraction: e.target.value as Options["extraction"] })}>
                <option value="">predefinita</option>
                <option value="docling">docling</option>
                <option value="vlm">VLM</option>
              </select>
            </label>
          </div>
          <h3>Modelli VLM per fase</h3>
          <div className="griglia-opzioni">
            {FASI_VLM.map((f) => (
              <label key={f}>
                {nomeFase(f)}
                <select value={o.models[f] ?? ""} onChange={(e) => setO({ ...o, models: { ...o.models, [f]: e.target.value } })}
                        onFocus={leggiModelli}>
                  <option value="">predefinito{mod ? ` (${mod.defaults[f]})` : ""}</option>
                  {mod?.models.filter((m) => m.name !== mod.defaults[f]).map((m) => (
                    <option key={m.name} value={m.name} disabled={m.present !== true}>
                      {m.name}{m.present === false ? " (assente nel backend)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {mod?.error && <p className="nota errore">elenco dei modelli non disponibile: {mod.error}</p>}
          {cat && (
            <details style={{ marginTop: 10 }}>
              <summary>Gruppi di categorie ({o.disable.length + o.enable.length} modifiche)</summary>
              <div className="griglia-opzioni" style={{ marginTop: 8 }}>
                {Object.entries(cat.groups).map(([g, cs]) => {
                  const attivo = cs.some((c) => cat.active.includes(c));
                  const campo = attivo ? "disable" : "enable";
                  const scelto = o[campo].includes(g);
                  return (
                    <label key={g} className="inline" title={cs.join(", ")}>
                      <input type="checkbox" checked={attivo !== scelto} onChange={() => alterna(campo, g)} /> {g}
                      <span className="tenue"> ({cs.length})</span>
                    </label>
                  );
                })}
              </div>
            </details>
          )}
          <label className="inline" style={{ marginTop: 12 }}>
            <input type="checkbox" checked={o.transcript} onChange={(e) => setO({ ...o, transcript: e.target.checked })} />
            Restituisci anche la trascrizione in chiaro (debug)
          </label>
          {o.transcript && (
            <p className="avviso-rosso">
              Il testo IN CHIARO del documento viaggia nella risposta e resta in memoria sul server fino alla scadenza del
              lavoro. Usalo solo per prove; con la modalità pseudonimo il confronto funziona anche senza, con la chiave.
            </p>
          )}
        </div>
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
                <td>{nomeModalita(d.opzioni.mode)}{d.opzioni.transcript ? " + trascrizione" : ""}{modelliScelti(d.opzioni).length > 0 && <div className="nota">{modelliScelti(d.opzioni).join(" · ")}</div>}</td>
                <td><Avanzamento d={d} /></td>
                <td className="num"><Durata d={d} /></td>
                <td>
                  <div className="riga">
                    {d.risultato && <Link to={`/confronto/${d.id}`}>Confronto</Link>}
                    {d.risultato && <Link to={`/dashboard/${d.id}`}>Dashboard</Link>}
                    {d.risultato && <button className="secondario piccolo" onClick={() => scaricaZip(d)}>ZIP</button>}
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
