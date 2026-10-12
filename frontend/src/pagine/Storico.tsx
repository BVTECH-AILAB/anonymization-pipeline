// Storico delle esecuzioni salvate in questo browser (solo report e metriche, nessun valore): tabella,
// confronto tra configurazioni, esportazione CSV.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BarreImpilate, BarrePerGruppo } from "../componenti/Grafici";
import { nomeEsito, nomeFase, nomeModalita } from "../lib/nomi";
import { scaricaBlob } from "../lib/scarica";
import * as storico from "../lib/storico";

const FASI = ["extraction", "detection", "redaction", "verification"]; // chiavi di times_s
const SERIE_FASI = FASI.map(nomeFase);
const MAX_GRUPPI = 5; // oltre: "altri" (niente colori generati o riciclati)

function scaricaCsv(testo: string) {
  scaricaBlob(
    new Blob(["﻿" + testo], { type: "text/csv;charset=utf-8" }),
    `storico-anonimizzatore-${new Date().toISOString().slice(0, 10)}.csv`,
  );
}

const configurazione = (e: storico.Esecuzione) => `${e.modello}${e.estrazione ? " · " + e.estrazione : ""}`;

export default function Storico() {
  const [tutte, setTutte] = useState<storico.Esecuzione[]>([]);
  const [filtro, setFiltro] = useState("");
  const [scelte, setScelte] = useState<Set<string>>(new Set());
  const ricarica = () => storico.elenco().then(setTutte);
  useEffect(() => { ricarica(); }, []);

  const visibili = useMemo(() => {
    const f = filtro.toLowerCase();
    return tutte.filter((e) => !f || [e.etichetta, e.modello, e.estrazione, nomeModalita(e.modalita), e.impronta].some((x) => x.toLowerCase().includes(f)));
  }, [tutte, filtro]);
  const confronto = scelte.size ? visibili.filter((e) => scelte.has(e.id)) : visibili;

  // configurazioni (modello · estrazione) in ordine di prima apparizione: il colore segue la configurazione
  const gruppi = useMemo(() => {
    const tutti = [...new Set([...confronto].reverse().map(configurazione))];
    return tutti.length > MAX_GRUPPI ? [...tutti.slice(0, MAX_GRUPPI - 1), "altri"] : tutti;
  }, [confronto]);
  const gruppoDi = (e: storico.Esecuzione) => (gruppi.includes(configurazione(e)) ? configurazione(e) : "altri");

  const perEsecuzione = useMemo(() => [...confronto].reverse().map((e, i) => ({
    esecuzione: e.etichetta || `#${i + 1}`,
    [gruppoDi(e)]: e.s_per_pagina !== null ? Number(e.s_per_pagina.toFixed(2)) : null,
  })), [confronto, gruppi]);

  const mediePerConfigurazione = useMemo(() => gruppi.map((g) => {
    const es = confronto.filter((e) => gruppoDi(e) === g);
    return {
      nome: g,
      ...Object.fromEntries(FASI.map((f, i) => [SERIE_FASI[i], Number((es.reduce((s, e) => s + (e.report.times_s[f] ?? 0) / Math.max(1, e.pagine), 0) / Math.max(1, es.length)).toFixed(2))])),
    };
  }), [confronto, gruppi]);

  const alterna = (id: string) => setScelte((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <section>
      <div className="barra-strumenti">
        <h1 style={{ margin: 0 }}>Storico</h1>
        <input placeholder="filtra (etichetta, modello, estrazione…)" value={filtro} onChange={(e) => setFiltro(e.target.value)} />
        <button className="secondario" disabled={!confronto.length} onClick={() => scaricaCsv(storico.csv(confronto))}>
          Esporta CSV {scelte.size ? `(${scelte.size} scelte)` : ""}
        </button>
        <button className="secondario" disabled={!tutte.length} onClick={async () => { if (confirm("Cancellare tutto lo storico di questo browser?")) { await storico.svuota(); setScelte(new Set()); ricarica(); } }}>
          Svuota
        </button>
      </div>
      <p className="nota">
        Esecuzioni salvate in questo browser: solo report e metriche (nessun valore del documento né nome del file). I documenti
        completati vengono salvati automaticamente; altri report si aggiungono dalla Dashboard. Seleziona le righe per
        restringere il confronto.
      </p>

      {confronto.length > 0 && (
        <div className="griglia-2" style={{ marginBottom: 14 }}>
          <div className="scheda">
            <h2 style={{ marginTop: 0 }}>Secondi per pagina, per esecuzione</h2>
            <BarrePerGruppo dati={perEsecuzione} chiave="esecuzione" gruppi={gruppi} unita=" s" />
          </div>
          <div className="scheda">
            <h2 style={{ marginTop: 0 }}>Secondi per pagina per fase, media per configurazione</h2>
            <BarreImpilate dati={mediePerConfigurazione} serie={SERIE_FASI} unita=" s" />
          </div>
        </div>
      )}

      {visibili.length === 0 ? (
        <p className="tenue">Nessuna esecuzione salvata.</p>
      ) : (
        <table className="tabella">
          <thead>
            <tr>
              <th /><th>Data</th><th>Etichetta</th><th>Modello</th><th>Estrazione</th><th>Modalità</th>
              <th className="num">Pagine</th><th className="num">Totale (s)</th><th className="num">s/pagina</th><th>Verifica</th><th>Impronta</th><th />
            </tr>
          </thead>
          <tbody>
            {visibili.map((e) => (
              <tr key={e.id}>
                <td><input type="checkbox" checked={scelte.has(e.id)} onChange={() => alterna(e.id)} aria-label="Seleziona per il confronto" /></td>
                <td>{new Date(e.salvato).toLocaleString("it-IT")}</td>
                <td>
                  <input defaultValue={e.etichetta} placeholder="etichetta" aria-label="Etichetta"
                         onBlur={async (x) => { if (x.target.value !== e.etichetta) { await storico.rinomina(e.id, x.target.value); ricarica(); } }} />
                </td>
                <td>{e.modello}</td>
                <td>{e.estrazione || "–"}</td>
                <td>{nomeModalita(e.modalita)}</td>
                <td className="num">{e.pagine}</td>
                <td className="num">{e.totale_s.toFixed(1)}</td>
                <td className="num">{e.s_per_pagina?.toFixed(1) ?? "–"}</td>
                <td>{nomeEsito(e.esito_verifica)}</td>
                <td className="mono">{e.impronta.slice(0, 12)}</td>
                <td>
                  <div className="riga">
                    <Link to={`/dashboard?esecuzione=${e.id}`}>Dashboard</Link>
                    <button className="secondario piccolo" onClick={async () => { await storico.elimina(e.id); ricarica(); }}>×</button>
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
