// Dashboard di un documento: report e metriche. Fonte: un documento della sessione, un'esecuzione dello
// storico (?esecuzione=id) oppure file caricati (report.json, metriche.json o lo ZIP prodotto dall'API).
import JSZip from "jszip";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { Metrics, Report } from "../api/tipi";
import { BarreImpilate, BarreOrizzontali } from "../componenti/Grafici";
import { etichettaModelli, FASI_VLM, nomeEsito, nomeFase, nomeFonte, nomeModalita, nomeMotivoVlm, nomeVerifica, testoAvviso } from "../lib/nomi";
import * as storico from "../lib/storico";
import { useSessione } from "../stato/sessione";

interface Dati { report: Report; metriche: Metrics | null; titolo: string }

// file di questa versione del servizio: le precedenti hanno le chiavi in italiano o un solo modello VLM
const oggetto = (j: unknown): j is Record<string, unknown> => !!j && typeof j === "object";
const eReport = (j: unknown): j is Report =>
  oggetto(j) && "counts" in j && "times_s" in j && oggetto(j.model) && oggetto(j.model.phases);
const eMetriche = (j: unknown): j is Metrics =>
  oggetto(j) && "throughput" in j && oggetto(j.configuration) && oggetto(j.configuration.models);

const n = (v: number | null | undefined, cifre = 1) =>
  v === null || v === undefined ? "–" : v.toLocaleString("it-IT", { maximumFractionDigits: cifre });

function Riquadro({ etichetta, valore, nota }: { etichetta: string; valore: string; nota?: string }) {
  return (
    <div className="riquadro">
      <div className="etichetta">{etichetta}</div>
      <div className="valore">{valore}</div>
      {nota && <div className="nota">{nota}</div>}
    </div>
  );
}

async function leggiFile(files: File[]): Promise<Dati> {
  const letti: unknown[] = [];
  for (const f of files) {
    if (/\.zip$/i.test(f.name)) {
      const z = await JSZip.loadAsync(f);
      for (const [nome, voce] of Object.entries(z.files)) {
        if (nome.endsWith(".report.json") || nome.endsWith(".metriche.json")) letti.push(JSON.parse(await voce.async("string")));
      }
    } else if (/\.json$/i.test(f.name)) {
      letti.push(JSON.parse(await f.text()));
    }
  }
  const report = letti.findLast(eReport);
  if (!report) throw new Error("Serve un .report.json di questa versione del servizio (o lo ZIP che lo contiene)");
  return { report, metriche: letti.findLast(eMetriche) ?? null, titolo: files.map((f) => f.name).join(", ") };
}

export default function Dashboard() {
  const { id } = useParams();
  const [q] = useSearchParams();
  const naviga = useNavigate();
  const s = useSessione();
  const pronti = s.documenti.filter((d) => d.risultato);
  const [esterni, setEsterni] = useState<Dati | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [salvato, setSalvato] = useState(false);

  useEffect(() => {
    const e = q.get("esecuzione");
    if (!e) return;
    storico.elenco().then((tutte) => {
      const x = tutte.find((y) => y.id === e);
      if (x) setEsterni({ report: x.report, metriche: x.metriche, titolo: x.etichetta || `esecuzione del ${new Date(x.salvato).toLocaleString("it-IT")}` });
    });
  }, [q]);

  const doc = pronti.find((x) => x.id === id) ?? (!id && !q.get("esecuzione") && !esterni ? pronti[pronti.length - 1] : undefined);
  const dati: Dati | null = esterni ?? (doc?.risultato ? { report: doc.risultato.report, metriche: doc.risultato.metrics, titolo: doc.nome } : null);

  return (
    <section>
      <div className="barra-strumenti">
        <h1 style={{ margin: 0 }}>Dashboard</h1>
        {pronti.length > 0 && (
          <select value={esterni ? "" : doc?.id ?? ""} onChange={(e) => { setEsterni(null); naviga(`/dashboard/${e.target.value}`); }} aria-label="Documento">
            <option value="" disabled>documento della sessione…</option>
            {pronti.map((x) => <option key={x.id} value={x.id}>{x.nome}</option>)}
          </select>
        )}
        <label className="inline">
          Apri report/metriche/ZIP:&nbsp;
          <input type="file" multiple accept=".json,.zip" onChange={async (e) => {
            setErrore(null); setSalvato(false);
            try { setEsterni(await leggiFile(Array.from(e.target.files ?? []))); } catch (x) { setErrore((x as Error).message); }
            e.target.value = "";
          }} />
        </label>
        {esterni && !q.get("esecuzione") && (
          <button className="secondario piccolo" disabled={salvato} onClick={async () => {
            await storico.salva(storico.daRisultato(esterni.report, esterni.metriche, prompt("Etichetta dell'esecuzione") ?? ""));
            setSalvato(true);
          }}>{salvato ? "Salvato nello storico" : "Salva nello storico"}</button>
        )}
        {doc && !esterni && <Link to={`/confronto/${doc.id}`}>Confronto</Link>}
      </div>
      {errore && <p className="errore">{errore}</p>}
      {dati ? <Contenuto {...dati} /> : <p className="tenue">Nessun documento: <Link to="/carica">caricane uno</Link> oppure apri un report.</p>}
    </section>
  );
}

const FASI = ["ingest", "extraction", "detection", "redaction", "verification", "slot_wait"];

function Contenuto({ report: r, metriche: m, titolo }: Dati) {
  const tempi = m?.times_s ?? r.times_s;
  const vlm = m?.vlm ?? r.vlm;
  const totVlm = Object.values(vlm).reduce((a, x) => ({ chiamate: a.chiamate + x.calls, tin: a.tin + x.tokens_in, tout: a.tout + x.tokens_out }), { chiamate: 0, tin: 0, tout: 0 });
  const conteggi = useMemo(() => Object.entries(r.counts).map(([nome, valore]) => ({ nome, valore })).sort((a, b) => b.valore - a.valore), [r]);
  const residui = useMemo(() => {
    const per: Record<string, number> = {};
    for (const x of r.verification.residuals) per[x.category] = (per[x.category] ?? 0) + 1;
    return Object.entries(per).map(([nome, valore]) => ({ nome, valore })).sort((a, b) => b.valore - a.valore);
  }, [r]);
  const pagineResidui = [...new Set(r.verification.residuals.map((x) => x.page))].sort((a, b) => a - b);
  const rilevatori = m ? m.configuration.detectors : Object.keys(r.detection.sources);
  const perPagina = useMemo(() => (m?.pages ?? []).map((p) => ({
    nome: `p. ${p.page}`,
    ...(p.extraction_s !== null ? { "estrazione VLM": p.extraction_s } : {}),
    ...Object.fromEntries(Object.entries(p.detection_s).map(([k, v]) => [`rilevamento ${nomeFonte(k)}`, v])),
    verifica: p.verification_s,
  })), [m]);
  const seriePagine = useMemo(() => {
    const tutte = new Set<string>();
    perPagina.forEach((p) => Object.keys(p).forEach((k) => k !== "nome" && tutte.add(k)));
    return [...tutte].filter((k) => perPagina.some((p) => Number((p as Record<string, unknown>)[k]) > 0.05)).slice(0, 6);
  }, [perPagina]);

  return (
    <>
      <p className="nota">
        {titolo} · impronta <span className="mono">{r.document.sha256.slice(0, 12)}</span> ·{" "}
        <span title={FASI_VLM.map((f) => `${nomeFase(f)}: ${r.model.phases[f]}`).join("\n")}>
          {etichettaModelli(r.model)}
        </span>{" "}({r.model.backend}) ·
        modalità {nomeModalita(r.mode)}{m ? ` · estrazione ${m.configuration.extraction} · concorrenza ${m.configuration.concurrency}` : ""}
      </p>
      <div className="riquadri">
        <Riquadro etichetta="Tempo totale" valore={`${n(tempi.total)} s`} nota={m?.times_s.slot_wait ? `+ ${n(m.times_s.slot_wait)} s in coda` : undefined} />
        <Riquadro etichetta="Pagine" valore={String(r.document.pages)} nota={`${r.extraction.docling.length} docling · ${r.extraction.vlm.length} VLM`} />
        <Riquadro etichetta="Secondi per pagina" valore={n(m?.throughput.s_per_page ?? (tempi.total ?? 0) / Math.max(1, r.document.pages))} />
        <Riquadro etichetta="Verifica" valore={nomeEsito(r.verification.outcome)} nota={`modalità ${nomeVerifica(r.verification.mode)} · ${r.verification.residuals.length} ${r.verification.residuals.length === 1 ? "residuo" : "residui"}`} />
        <Riquadro etichetta="Valori censurati" valore={String(Object.values(r.counts).reduce((a, b) => a + b, 0))} nota={`${r.entities.length} entità`} />
        <Riquadro etichetta="Chiamate al VLM" valore={String(totVlm.chiamate)} nota={`${n(totVlm.tin, 0)} token in · ${n(totVlm.tout, 0)} out`} />
        {m && <Riquadro etichetta="CPU / memoria" valore={`${n(m.resources.process_cpu_s)} s`} nota={`picco ${n(m.resources.process_peak_rss_mb, 0)} MB (processo)`} />}
      </div>

      {r.warnings.length > 0 && (
        <div className="scheda" style={{ marginBottom: 14 }}>
          <strong>Avvisi:</strong> {r.warnings.map(testoAvviso).join(" · ")}
        </div>
      )}

      <div className="griglia-2">
        <div className="scheda">
          <h2 style={{ marginTop: 0 }}>Tempo per fase</h2>
          <BarreOrizzontali nome="secondi" unita=" s" dati={FASI.filter((f) => tempi[f]).map((f) => ({ nome: nomeFase(f), valore: tempi[f] }))} />
        </div>
        <div className="scheda">
          <h2 style={{ marginTop: 0 }}>Valori censurati per categoria</h2>
          {conteggi.length ? <BarreOrizzontali nome="occorrenze" dati={conteggi} /> : <p className="tenue">Nessun valore censurato.</p>}
        </div>
        {residui.length > 0 && (
          <div className="scheda residui">
            <h2 style={{ marginTop: 0 }}>Residui per categoria</h2>
            <BarreOrizzontali nome="residui" dati={residui} />
            <p className="nota">Dalla verifica: valori rimasti in chiaro nel testo censurato · pagine {pagineResidui.join(", ")}. Il dettaglio è nel Confronto.</p>
          </div>
        )}
      </div>

      {m && perPagina.length > 0 && (
        <div className="scheda" style={{ marginTop: 14 }}>
          <h2 style={{ marginTop: 0 }}>Tempo per pagina</h2>
          <p className="nota">Tempo di elaborazione dei componenti su ogni pagina; per il VLM è la durata delle chiamate, senza l'attesa in coda (le chiamate girano in parallelo, quindi la somma può superare la durata della fase).</p>
          <BarreImpilate dati={perPagina} serie={seriePagine} unita=" s" />
        </div>
      )}

      <div className="griglia-2" style={{ marginTop: 14 }}>
        <div className="scheda">
          <h2 style={{ marginTop: 0 }}>Rilevatori</h2>
          <table className="tabella">
            <thead><tr><th>Rilevatore</th><th className="num">Valori</th>{m && <><th className="num">Tempo (s)</th><th className="num">Max pagina (s)</th></>}</tr></thead>
            <tbody>
              {rilevatori.map((k) => {
                const x = m?.detection[k];
                const det = typeof x === "object" ? x : null;
                return (
                  <tr key={k}>
                    <td>{nomeFonte(k)}</td>
                    <td className="num">{r.detection.sources[k] ?? "–"}</td>
                    {m && <><td className="num">{n(det?.duration_s, 2)}</td><td className="num">{n(det?.max_page_s, 2)}</td></>}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="nota">
            {r.detection.values} valori ancorati · {r.detection.hallucinations_discarded} allucinazioni scartate ·
            {" "}{r.detection.eponyms_discarded} eponimi scartati · {r.detection.inactive_categories} di categorie non attive
          </p>
          <h3>Estrazione</h3>
          <p className="nota">
            docling: {r.extraction.docling.length ? `pagine ${r.extraction.docling.join(", ")}` : "nessuna pagina"}
            {m ? ` (${n(m.extraction.docling.duration_s)} s)` : ""}
            {r.extraction.vlm.length > 0 && <> · VLM: {r.extraction.vlm.map((p) => `p. ${p.page} (${nomeMotivoVlm(p.reason)})`).join(", ")}</>}
          </p>
          {m && (
            <>
              <h3>Sotto-fasi</h3>
              <table className="tabella">
                <tbody>
                  <tr><td>Codifica delle immagini</td><td className="num">{n(m.extraction.image_encoding_s, 2)} s</td></tr>
                  <tr><td>Unione dei rilevatori</td><td className="num">{n(m.detection.union_s as number, 3)} s</td></tr>
                  <tr><td>Sostituzione</td><td className="num">{n(m.redaction.replacement_s, 3)} s</td></tr>
                </tbody>
              </table>
            </>
          )}
        </div>

        <div className="scheda">
          <h2 style={{ marginTop: 0 }}>VLM per fase</h2>
          <table className="tabella">
            <thead>
              <tr><th>Fase</th><th className="num">Chiamate</th><th className="num">Token in/out</th><th className="num">Latenza p50 / p95 / max (s)</th><th className="num">Coda max (s)</th><th className="num">Token/s</th></tr>
            </thead>
            <tbody>
              {Object.entries(vlm).map(([f, x]) => (
                <tr key={f}>
                  <td>{nomeFase(f)}</td>
                  <td className="num">{x.calls}{x.errors ? <span className="errore"> ({x.errors} err.)</span> : ""}{x.truncated ? <span className="errore"> ({x.truncated} tronc.)</span> : ""}</td>
                  <td className="num">{n(x.tokens_in, 0)} / {n(x.tokens_out, 0)}</td>
                  <td className="num">{x.latency_s ? `${n(x.latency_s.p50)} / ${n(x.latency_s.p95)} / ${n(x.latency_s.max)}` : `tot. ${n(x.duration_s)}`}</td>
                  <td className="num">{n(x.queue_wait_s?.max)}</td>
                  <td className="num">{n(x.tokens_out_per_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
          <summary><strong>Entità ({r.entities.length})</strong> <span className="nota">tag, categoria, varianti, occorrenze e rilevatori che le hanno trovate</span></summary>
          <div style={{ maxHeight: 420, overflow: "auto", marginTop: 6 }}>
          <table className="tabella">
            <thead><tr><th>Tag</th><th>Categoria</th><th className="num">Varianti</th><th className="num">Occorrenze</th><th>Fonti</th></tr></thead>
            <tbody>
              {r.entities.map((e) => (
                <tr key={e.replacement}><td className="mono">{e.replacement}</td><td>{e.category}</td><td className="num">{e.variants}</td><td className="num">{e.occurrences ?? "–"}</td><td>{e.sources.map(nomeFonte).join(", ")}</td></tr>
              ))}
            </tbody>
          </table>
          </div>
          </details>
        </div>
      </div>
    </>
  );
}
