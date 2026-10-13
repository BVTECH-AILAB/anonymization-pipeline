// Validazione sul dataset sintetico (validation/): configurazione della run (dataset, profili, soglie),
// avvio, avanzamento dal vivo, risultati e report, run precedenti. Usa l'API di validazione locale.
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useNavigate, useParams } from "react-router-dom";
import * as api from "../api/client";
import type { Categories, Mode, Models, Options, Status } from "../api/tipi";
import * as v from "../api/validazione";
import type { ConfigIniziale, ConfigValidazione, EsitoProfilo, Run, RunInElenco, Soglie } from "../api/validazione";
import { BarreOrizzontali } from "../componenti/Grafici";
import OpzioniRichiesta from "../componenti/OpzioniRichiesta";
import { nomeFase } from "../lib/nomi";
import { scaricaBlob } from "../lib/scarica";
import {
  daInviare,
  DIFFICOLTA,
  nuovoNome,
  problemi,
  profiliUI,
  selezionati,
  stimaMinuti,
  TIPI,
  type Filtri,
  type ProfiloUI,
} from "../lib/validazione";

const pct = (x: number | null | undefined) =>
  x === null || x === undefined ? "–" : `${(x * 100).toLocaleString("it-IT", { maximumFractionDigits: 1 })}%`;
const num = (x: number | null | undefined) =>
  x === null || x === undefined ? "–" : x.toLocaleString("it-IT", { maximumFractionDigits: 1 });
const ESITO: Record<string, string> = { true: "PASS", false: "FAIL", null: "n.d." };
const STATI: Record<string, string> = {
  in_corso: "in corso",
  completata: "completata",
  annullata: "annullata",
  interrotta: "interrotta",
  sconosciuto: "sconosciuto",
};
const DOC_STATO: Record<string, string> = {
  completed: "OK",
  rejected: "RESPINTO",
  error: "ERRORE",
  timeout: "TIMEOUT",
  cancelled: "ANNULLATO",
};
const SOGLIE: [keyof Soglie, string, string][] = [
  ["recall_min", "Recall minima", ">="],
  ["tasso_fuga_max", "Tasso di fuga massimo", "<="],
  ["precision_min", "Precision minima", ">="],
  ["f1_min", "F1 minima", ">="],
];

function Riquadro({ etichetta, valore, nota, classe }: { etichetta: string; valore: string; nota?: string; classe?: string }) {
  return (
    <div className="riquadro">
      <div className="etichetta">{etichetta}</div>
      <div className={`valore ${classe ?? ""}`}>{valore}</div>
      {nota && <div className="nota">{nota}</div>}
    </div>
  );
}

function Barra({ fatto, totale }: { fatto: number; totale: number }) {
  return (
    <div className="barra-avanzamento" role="progressbar" aria-valuemin={0} aria-valuemax={totale} aria-valuenow={fatto}>
      <div style={{ width: `${totale ? (100 * fatto) / totale : 0}%` }} />
    </div>
  );
}

const alternaVoce = (lista: string[], x: string) => (lista.includes(x) ? lista.filter((y) => y !== x) : [...lista, x]);

// --- nuova validazione --------------------------------------------------------------------------------

function NuovaValidazione({ iniziale, preset }: { iniziale: ConfigIniziale; preset: ConfigValidazione | null }) {
  const naviga = useNavigate();
  const [cat, setCat] = useState<Categories | null>(null);
  const [mod, setMod] = useState<Models | null>(null);
  const [stato, setStato] = useState<Status | null>(null);
  const [profili, setProfili] = useState<ProfiloUI[]>([]);
  const [filtri, setFiltri] = useState<Filtri>({ tipi: [], difficolta: [], documenti: [] });
  const [soglie, setSoglie] = useState<Soglie>(iniziale.config.soglie);
  const [esecuzione, setEsecuzione] = useState({ concorrenza: 1, timeout_documento_s: 1800 });
  const [chiave, setChiave] = useState("");
  const [cercaDoc, setCercaDoc] = useState("");
  const [errore, setErrore] = useState<string | null>(null);
  const [invio, setInvio] = useState(false);

  const leggiModelli = () => api.modelli().then(setMod).catch(() => setMod(null));
  const leggiStato = () => api.salute().then(setStato).catch(() => setStato(null));
  useEffect(() => {
    api.categorie().then(setCat).catch(() => setCat(null));
    leggiModelli();
    leggiStato();
  }, []);

  const modoServizio = (stato?.mode ?? "tag") as Mode;
  const carica = (c: ConfigValidazione) => {
    setProfili(profiliUI(c, modoServizio, Date.now()));
    setFiltri({ tipi: c.dataset.tipi, difficolta: c.dataset.difficolta, documenti: c.dataset.documenti });
    setSoglie(c.soglie);
    setEsecuzione({ concorrenza: c.server.concorrenza, timeout_documento_s: c.server.timeout_documento_s });
  };
  // al primo stato del servizio (serve il suo modo predefinito) e quando arriva una configurazione da riusare
  useEffect(() => { carica(preset ?? iniziale.config); }, [preset, iniziale, stato?.mode]);

  const docs = selezionati(iniziale.documents, filtri);
  const pagine = docs.reduce((n, d) => n + d.pages, 0);
  const servePseudonimo = profili.some((p) => p.o.mode === "pseudonym");
  const chiaveMancante = servePseudonimo && !iniziale.key_available && !chiave;
  const bloccanti = problemi(profili, docs.length, chiaveMancante);

  const setOpzioni = (k: number): React.Dispatch<React.SetStateAction<Options>> => (u) =>
    setProfili((ps) => ps.map((p) => (p.chiave === k ? { ...p, o: typeof u === "function" ? u(p.o) : u } : p)));
  const aggiorna = (k: number, campi: Partial<ProfiloUI>) =>
    setProfili((ps) => ps.map((p) => (p.chiave === k ? { ...p, ...campi } : p)));
  const aggiungi = (da?: ProfiloUI) =>
    setProfili((ps) => {
      const base = da ?? profiliUI({ ...iniziale.config, profili: [{ nome: "x", descrizione: "" }] }, modoServizio)[0];
      return [...ps, { ...base, chiave: Date.now(), nome: nuovoNome(ps, da ? da.nome : "profilo"), o: { ...base.o } }];
    });

  const avvia = async () => {
    setErrore(null);
    setInvio(true);
    try {
      const config = daInviare(iniziale.config, profili, filtri, soglie, esecuzione);
      const { id } = await v.avvia(config, chiave || null);
      naviga(`/validazione/${id}`);
    } catch (e) {
      setErrore((e as Error).message);
    } finally {
      setInvio(false);
    }
  };

  const candidati = iniziale.documents.filter(
    (d) =>
      (!filtri.tipi.length || filtri.tipi.includes(d.type)) &&
      (!filtri.difficolta.length || filtri.difficolta.includes(d.difficulty)) &&
      (!cercaDoc || d.id.includes(cercaDoc.trim().toLowerCase())),
  );

  return (
    <>
      <div className="scheda">
        <div className="riga" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Nuova validazione</h2>
          <button className="secondario piccolo" onClick={() => carica(iniziale.config)}>ripristina default (config.yaml)</button>
        </div>

        <h3>Dataset</h3>
        <div className="griglia-2">
          <fieldset>
            <legend>Tipologie <span className="tenue">(nessuna = tutte)</span></legend>
            {Object.entries(TIPI).map(([t, nome]) => (
              <label key={t} className="inline">
                <input type="checkbox" checked={filtri.tipi.includes(t)}
                       onChange={() => setFiltri((f) => ({ ...f, tipi: alternaVoce(f.tipi, t) }))} /> {nome}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>Difficoltà <span className="tenue">(nessuna = tutte)</span></legend>
            {DIFFICOLTA.map((d) => (
              <label key={d} className="inline">
                <input type="checkbox" checked={filtri.difficolta.includes(d)}
                       onChange={() => setFiltri((f) => ({ ...f, difficolta: alternaVoce(f.difficolta, d) }))} /> {d}
              </label>
            ))}
          </fieldset>
        </div>
        <details className="sezione">
          <summary>
            <span className="sezione-titolo">Documenti specifici</span>
            <span className="tenue sezione-riassunto">{filtri.documenti.length ? `${filtri.documenti.length} scelti` : "tutti quelli dei filtri"}</span>
          </summary>
          <div className="sezione-corpo">
            <div className="riga">
              <input type="search" placeholder="cerca per id" value={cercaDoc} onChange={(e) => setCercaDoc(e.target.value)} aria-label="Cerca un documento" />
              <button className="secondario piccolo" onClick={() => setFiltri((f) => ({ ...f, documenti: [] }))}>nessuno</button>
            </div>
            <div className="griglia-categorie">
              {candidati.map((d) => (
                <label key={d.id} className="inline" title={`${d.pages} pagine, ${d.entities} valori`}>
                  <input type="checkbox" checked={filtri.documenti.includes(d.id)}
                         onChange={() => setFiltri((f) => ({ ...f, documenti: alternaVoce(f.documenti, d.id) }))} /> {d.id}
                </label>
              ))}
            </div>
          </div>
        </details>
        <p className="nota">
          {docs.length} documenti, {pagine} pagine, {profili.length} {profili.length === 1 ? "profilo" : "profili"}: durata stimata circa{" "}
          {stimaMinuti(docs, profili.length)} minuti (VLM su GPU).
        </p>

        <h3>Soglie</h3>
        <div className="griglia-opzioni">
          {SOGLIE.map(([k, nome, op]) => (
            <label key={k}>
              {nome} ({op} %)
              <input type="number" min={0} max={100} step={0.5} value={Math.round(soglie[k] * 1000) / 10}
                     onChange={(e) => setSoglie((s) => ({ ...s, [k]: Math.min(100, Math.max(0, Number(e.target.value))) / 100 }))} />
            </label>
          ))}
        </div>

        <h3>Esecuzione</h3>
        <div className="griglia-opzioni">
          <label>
            Documenti in parallelo
            <input type="number" min={1} max={16} value={esecuzione.concorrenza}
                   onChange={(e) => setEsecuzione((x) => ({ ...x, concorrenza: Math.max(1, Number(e.target.value)) }))} />
          </label>
          <label>
            Timeout per documento (s)
            <input type="number" min={60} step={60} value={esecuzione.timeout_documento_s}
                   onChange={(e) => setEsecuzione((x) => ({ ...x, timeout_documento_s: Math.max(1, Number(e.target.value)) }))} />
          </label>
          {servePseudonimo && !iniziale.key_available && (
            <label>
              Chiave per la modalità pseudonimo
              <input type="password" autoComplete="off" value={chiave} onChange={(e) => setChiave(e.target.value)} />
            </label>
          )}
        </div>
        {servePseudonimo && iniziale.key_available && <p className="nota">Modalità pseudonimo: si usa la chiave del servizio di validazione (ANONVAL_KEY).</p>}
      </div>

      {profili.map((p) => (
        <div key={p.chiave} className="profilo-validazione">
          <div className="scheda">
            <div className="riga" style={{ justifyContent: "space-between" }}>
              <div className="riga" style={{ flex: 1 }}>
                <label>
                  Nome del profilo
                  <input value={p.nome} onChange={(e) => aggiorna(p.chiave, { nome: e.target.value })} aria-label="Nome del profilo" />
                </label>
                <label style={{ flex: 1 }}>
                  Descrizione
                  <input value={p.descrizione} onChange={(e) => aggiorna(p.chiave, { descrizione: e.target.value })} aria-label="Descrizione del profilo"
                         style={{ width: "100%" }} />
                </label>
              </div>
              <div className="riga">
                <button className="secondario piccolo" onClick={() => aggiungi(p)}>duplica</button>
                <button className="secondario piccolo pericolo" disabled={profili.length === 1}
                        onClick={() => setProfili((ps) => ps.filter((x) => x.chiave !== p.chiave))}>rimuovi</button>
              </div>
            </div>
          </div>
          <OpzioniRichiesta o={p.o} setO={setOpzioni(p.chiave)} predefinite={profiliUI(iniziale.config, modoServizio)[0]?.o ?? p.o}
                            cat={cat} mod={mod} stato={stato} leggiModelli={leggiModelli} riprovaStato={leggiStato}
                            titolo={`Parametri di ${p.nome || "profilo"}`} senzaTrascrizione />
        </div>
      ))}

      <div className="scheda">
        <div className="barra-strumenti">
          <button className="secondario" onClick={() => aggiungi()}>aggiungi profilo</button>
          <button onClick={avvia} disabled={invio || bloccanti.length > 0}>{invio ? "Controllo del servizio…" : "Avvia validazione"}</button>
        </div>
        {bloccanti.length > 0 && <ul className="nota errore">{bloccanti.map((b) => <li key={b}>{b}</li>)}</ul>}
        {errore && <p className="errore" style={{ whiteSpace: "pre-wrap" }}>{errore}</p>}
      </div>
    </>
  );
}

// --- run precedenti -----------------------------------------------------------------------------------

function ElencoRun({ runs, riusa }: { runs: RunInElenco[]; riusa: (id: string) => void }) {
  if (!runs.length) return <p className="tenue">Nessuna validazione eseguita.</p>;
  const filtro = (r: RunInElenco) =>
    [r.dataset.tipi?.join(", ") || "tutte le tipologie", r.dataset.difficolta?.join(", ") || "tutte le difficoltà",
     r.dataset.documenti?.length ? `${r.dataset.documenti.length} documenti` : ""].filter(Boolean).join(" · ");
  return (
    <table className="tabella">
      <thead>
        <tr><th>Run</th><th>Stato</th><th>Dataset</th><th>Profili ed esito</th><th /></tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.id}>
            <td><Link to={`/validazione/${r.id}`}>{r.id}</Link></td>
            <td>{STATI[r.stato] ?? r.stato}</td>
            <td>{filtro(r)}</td>
            <td>
              {r.profili.map((p) => (
                <span key={p} className="etichetta-profilo">
                  {p}{p in r.esiti ? ` ${ESITO[String(r.esiti[p])]}` : ""}
                </span>
              ))}
            </td>
            <td><button className="secondario piccolo" onClick={() => riusa(r.id)}>riusa configurazione</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- run: avanzamento e risultati ---------------------------------------------------------------------

function Avanzamento({ r }: { r: Run }) {
  const live = r.live;
  if (!live) return <p className="tenue">Avanzamento non disponibile (run di un processo precedente).</p>;
  return (
    <div className="scheda">
      <h3>Avanzamento</h3>
      <p>
        {live.completed}/{live.total} documenti
        {live.profile && <> · profilo in corso: <strong>{live.profile}</strong></>}
      </p>
      <Barra fatto={live.completed} totale={live.total} />
      {live.profiles.map((p) => (
        <div key={p} className="riga">
          <span style={{ minWidth: 140 }}>{p}</span>
          <Barra fatto={live.per_profile[p] ?? 0} totale={live.documents} />
          <span className="tenue">{live.per_profile[p] ?? 0}/{live.documents}</span>
        </div>
      ))}
      {live.running.length > 0 && (
        <>
          <h4>In elaborazione</h4>
          <ul>
            {live.running.map((d) => (
              <li key={d.document}>
                {d.document} · {d.phase ? nomeFase(d.phase) : d.status ?? "in invio"}
                {d.position ? ` · coda n. ${d.position}` : ""} · {d.seconds} s
              </li>
            ))}
          </ul>
        </>
      )}
      {live.error && <p className="errore">{live.error}</p>}
      {live.finished.length > 0 && (
        <details className="nota" open>
          <summary>Documenti completati ({live.finished.length})</summary>
          <table className="tabella">
            <thead>
              <tr><th>Profilo</th><th>Documento</th><th>Esito</th><th className="num">Recall</th><th className="num">Fughe</th><th className="num">Falsi pos.</th><th className="num">Secondi</th></tr>
            </thead>
            <tbody>
              {[...live.finished].reverse().map((d) => (
                <tr key={`${d.profile}-${d.document}`}>
                  <td>{d.profile}</td>
                  <td>{d.document}</td>
                  <td className={d.status === "completed" ? "" : "errore"} title={d.error ?? undefined}>{DOC_STATO[d.status] ?? d.status}</td>
                  <td className="num">{pct(d.recall)}</td>
                  <td className={`num ${d.leaks ? "errore" : ""}`}>{d.leaks}</td>
                  <td className="num">{d.false_positives}</td>
                  <td className="num">{num(d.seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function EsitoDiProfilo({ p }: { p: EsitoProfilo }) {
  const fughe = Object.entries(p.per_categoria)
    .filter(([, c]) => c.fughe > 0 && c.tasso_fuga !== null)
    .map(([nome, c]) => ({ nome, valore: (c.tasso_fuga ?? 0) * 100 }))
    .sort((a, b) => b.valore - a.valore);
  return (
    <div className="scheda">
      <h3>
        Profilo {p.nome}: <span className={p.esito === false ? "errore" : p.esito ? "ok" : ""}>{ESITO[String(p.esito)]}</span>
      </h3>
      {p.interrotto && <p className="errore">Interrotto: {p.interrotto}</p>}
      <div className="riquadri">
        <Riquadro etichetta="Recall" valore={pct(p.recall)} classe={p.soglie.recall_min === false ? "errore" : ""} />
        <Riquadro etichetta="Tasso di fuga" valore={pct(p.tasso_fuga)} classe={p.soglie.tasso_fuga_max === false ? "errore" : ""}
                  nota={`${p.documenti_con_fughe} documenti con fughe`} />
        <Riquadro etichetta="Precision" valore={pct(p.precision)} classe={p.soglie.precision_min === false ? "errore" : ""} />
        <Riquadro etichetta="F1" valore={pct(p.f1)} classe={p.soglie.f1_min === false ? "errore" : ""} />
        <Riquadro etichetta="Documenti" valore={`${p.documenti.completati}/${p.documenti.totali}`}
                  nota={`${p.documenti.respinti} respinti, ${p.documenti.errori} errori`} />
        <Riquadro etichetta="Tempo medio" valore={`${num(p.tempi_s.medio)} s`} nota={`p95 ${num(p.tempi_s.p95)} s`} />
      </div>
      <h4>Tasso di fuga per categoria</h4>
      {fughe.length ? <BarreOrizzontali dati={fughe} unita="%" nome="tasso di fuga" /> : <p className="tenue">Nessuna fuga.</p>}
    </div>
  );
}

function VistaRun({ id, riusa }: { id: string; riusa: (id: string) => void }) {
  const [r, setR] = useState<Run | null>(null);
  const [testo, setTesto] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [chiave, setChiave] = useState("");

  const leggi = () => v.run(id).then(setR).catch((e: Error) => setErrore(e.message));
  useEffect(() => {
    setR(null);
    setTesto(null);
    leggi();
  }, [id]);
  const inCorso = r?.stato === "in_corso";
  useEffect(() => {
    if (!inCorso) return;
    const t = setInterval(leggi, 2000);
    return () => clearInterval(t);
  }, [inCorso, id]);
  useEffect(() => {
    if (r && r.stato !== "in_corso") v.report(id).then(setTesto).catch(() => setTesto(null));
  }, [r?.stato, id]);

  const annulla = () => v.annulla(id).then(leggi).catch((e: Error) => setErrore(e.message));
  const riprendi = () => v.riprendi(id, chiave || null).then(leggi).catch((e: Error) => setErrore(e.message));
  const scaricaReport = () => testo && scaricaBlob(new Blob([testo], { type: "text/markdown" }), `validazione-${id}.md`);
  const scaricaMetriche = async () => {
    try {
      scaricaBlob(await v.metriche(id), `validazione-${id}.json`);
    } catch (e) {
      setErrore((e as Error).message);
    }
  };

  if (errore && !r) return <p className="errore">{errore}</p>;
  if (!r) return <p className="tenue">Caricamento…</p>;
  return (
    <>
      <div className="barra-strumenti">
        <Link to="/validazione">← nuova validazione</Link>
        <h2 style={{ margin: 0 }}>Run {id}</h2>
        <span className="tenue">{STATI[r.stato] ?? r.stato}</span>
        {inCorso && <button className="pericolo" onClick={annulla}>Annulla</button>}
        {(r.stato === "interrotta" || r.stato === "annullata") && (
          <>
            <input type="password" placeholder="chiave (solo pseudonimo)" value={chiave} onChange={(e) => setChiave(e.target.value)} aria-label="Chiave" />
            <button onClick={riprendi}>Riprendi</button>
          </>
        )}
        <button className="secondario" onClick={() => riusa(id)}>riusa configurazione</button>
        {testo && <button className="secondario" onClick={scaricaReport}>scarica report</button>}
        {!inCorso && <button className="secondario" onClick={scaricaMetriche}>scarica metriche</button>}
      </div>
      {errore && <p className="errore">{errore}</p>}
      {(inCorso || r.live) && <Avanzamento r={r} />}
      {r.profili.map((p) => <EsitoDiProfilo key={p.nome} p={p} />)}
      {testo && (
        <details className="scheda" open>
          <summary><strong>Report completo</strong></summary>
          <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{testo}</Markdown></div>
        </details>
      )}
    </>
  );
}

// --- pagina -------------------------------------------------------------------------------------------

export default function Validazione() {
  const { id } = useParams();
  const naviga = useNavigate();
  const [iniziale, setIniziale] = useState<ConfigIniziale | null>(null);
  const [runs, setRuns] = useState<RunInElenco[]>([]);
  const [preset, setPreset] = useState<ConfigValidazione | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    v.configIniziale().then(setIniziale).catch((e: Error) => setErrore(e.message));
  }, []);
  useEffect(() => {
    if (!id) v.elenco().then(setRuns).catch(() => setRuns([]));
  }, [id]);

  const riusa = (runId: string) =>
    v.configRun(runId)
      .then((c) => {
        setPreset(c);
        naviga("/validazione");
        window.scrollTo(0, 0);
      })
      .catch((e: Error) => setErrore(e.message));

  if (errore && !iniziale) {
    return (
      <div className="scheda">
        <h2>Validazione</h2>
        <p className="errore">Servizio di validazione non disponibile: {errore}</p>
        <p className="nota">Avvialo con <code>just up</code> (servizio <code>validation</code>) o <code>just validation-api</code>.</p>
      </div>
    );
  }
  if (!iniziale) return <p className="tenue">Caricamento…</p>;
  if (id) return <VistaRun id={id} riusa={riusa} />;
  return (
    <>
      <h2>Validazione</h2>
      <p className="tenue">
        Ogni documento del dataset sintetico (validation/) passa dal servizio con i parametri di ogni profilo e viene
        confrontato con la ground truth. Disponibile solo nello stack locale.
      </p>
      {errore && <p className="errore">{errore}</p>}
      <NuovaValidazione iniziale={iniziale} preset={preset} />
      <div className="scheda">
        <h2>Run precedenti</h2>
        <ElencoRun runs={runs} riusa={riusa} />
      </div>
    </>
  );
}
