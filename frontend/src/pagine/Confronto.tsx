// Confronto fianco a fianco: documento originale, Markdown in chiaro, Markdown censurato, pagina per pagina,
// con i tag evidenziati, i residui trovati dalla verifica e la revisione dell'operatore.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { categorie as leggiCategorie } from "../api/client";
import type { RestoreDetail } from "../api/tipi";
import VistaOriginale from "../componenti/VistaOriginale";
import VistaTesto from "../componenti/VistaTesto";
import { colore, type Intervallo } from "../lib/evidenzia";
import { etichettaModelli, nomeFonte } from "../lib/nomi";
import { dividiPagine, paginaDi } from "../lib/pagine";
import { esportaRevisione, type TipoVoce, type VoceRevisione } from "../lib/revisione";
import { scaricaBlob } from "../lib/scarica";
import { useSessione } from "../stato/sessione";

function scarica(nome: string, contenuto: string, tipo = "application/json") {
  scaricaBlob(new Blob([contenuto], { type: tipo }), nome);
}

export default function Confronto() {
  const { id } = useParams();
  const naviga = useNavigate();
  const s = useSessione();
  const pronti = s.documenti.filter((d) => d.risultato);
  const d = pronti.find((x) => x.id === id) ?? (id ? undefined : pronti[pronti.length - 1]);
  const r = d?.risultato;

  const [pagina, setPagina] = useState(1);
  const [resa, setResa] = useState(true);
  const [scelto, setScelto] = useState<Intervallo | null>(null);
  const [chiaro, setChiaro] = useState<RestoreDetail | null>(null);
  const [statoChiaro, setStatoChiaro] = useState<string>("");
  const [selezione, setSelezione] = useState<[number, number] | null>(null);
  const [categorieTutte, setCategorieTutte] = useState<string[]>([]);
  const [modulo, setModulo] = useState<{ categoria: string; nota: string }>({ categoria: "", nota: "" });
  const [salto, setSalto] = useState(0);

  useEffect(() => { setPagina(1); setScelto(null); setSelezione(null); }, [d?.id]);
  useEffect(() => {
    leggiCategorie().then((c) => setCategorieTutte(Object.values(c.groups).flat())).catch(() => undefined);
  }, []);

  // testo in chiaro: trascrizione o ripristino con la chiave
  useEffect(() => {
    setChiaro(null);
    if (!d?.risultato) return;
    if (!d.risultato.transcript && !d.risultato.encrypted_map_b64) {
      setStatoChiaro("Nessuna fonte del testo in chiaro: modalità tag o redazione senza trascrizione. Ricarica il documento in modalità pseudonimo oppure chiedendo la trascrizione.");
      return;
    }
    if (!d.risultato.transcript && !s.chiave) {
      setStatoChiaro("Imposta la chiave della mappa nel pannello Sessione per vedere il testo in chiaro.");
      return;
    }
    setStatoChiaro("Ripristino in corso…");
    s.testoInChiaro(d.id)
      .then((c) => { setChiaro(c); setStatoChiaro(c ? "" : "Testo in chiaro non disponibile."); })
      .catch((e) => setStatoChiaro(`Ripristino non riuscito: ${(e as Error).message}`));
  }, [d?.id, d?.risultato, s.chiave, s.testoInChiaro]);

  const pagCens = useMemo(() => (r ? dividiPagine(r.markdown) : []), [r]);
  const pagChiaro = useMemo(() => (chiaro ? dividiPagine(chiaro.text) : []), [chiaro]);
  const pc = pagCens.find((p) => p.numero === pagina) ?? pagCens[0];
  const pk = pagChiaro.find((p) => p.numero === pagina) ?? pagChiaro[0];

  const revisione = d?.revisione ?? [];
  const residui = useMemo<Intervallo[]>(
    () => (r?.report.verification.residuals ?? []).flatMap((x, i) =>
      x.start != null && x.end != null ? [{ inizio: x.start, fine: x.end, chiave: `res:${i}`, categoria: x.category, classe: "residuo" }] : []),
    [r],
  );
  const intCens = useMemo<Intervallo[]>(() => [
    ...(r?.tags ?? []).map((t) => ({ inizio: t.start, fine: t.end, chiave: t.replacement, categoria: t.category })),
    ...revisione.filter((v) => v.tipo === "sfuggito").map((v, i) => ({ inizio: v.inizio, fine: v.fine, chiave: `rev:${i}`, categoria: v.categoria, classe: "sfuggito" })),
    ...residui,
  ], [r, revisione, residui]);
  const intChiaro = useMemo<Intervallo[]>(
    () => (chiaro?.occurrences ?? []).map((o) => ({ inizio: o.original_start, fine: o.original_end, chiave: o.replacement, categoria: o.category })),
    [chiaro],
  );

  // scorrimento sincronizzato (in proporzione) tra i due testi
  const refCens = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (salto) refCens.current?.querySelector("mark.scelto")?.scrollIntoView({ block: "center" });
  }, [salto]);
  const refChiaro = useRef<HTMLDivElement>(null);
  const guida = useRef<"c" | "k" | null>(null);
  const sincronizza = useCallback((da: "c" | "k") => () => {
    if (guida.current && guida.current !== da) return;
    guida.current = da;
    const [a, b] = da === "c" ? [refCens.current, refChiaro.current] : [refChiaro.current, refCens.current];
    if (a && b) b.scrollTop = (a.scrollTop / Math.max(1, a.scrollHeight - a.clientHeight)) * (b.scrollHeight - b.clientHeight);
    requestAnimationFrame(() => { guida.current = null; });
  }, []);

  if (!d || !r) {
    return (
      <section>
        <h1>Confronto</h1>
        <p className="tenue">
          {id ? "Documento non trovato in questa sessione (i risultati restano solo nella scheda in cui li hai caricati)." : "Nessun documento completato."}{" "}
          <Link to="/carica">Carica un documento</Link>.
        </p>
      </section>
    );
  }

  const entita = r.report.entities.find((e) => e.replacement === scelto?.chiave);
  const originali = scelto ? [...new Set((chiaro?.occurrences ?? []).filter((o) => o.replacement === scelto.chiave).map((o) => o.original))] : [];
  const occorrenze = scelto ? r.tags.filter((t) => t.replacement === scelto.chiave).length : 0;

  const aggiungi = (v: VoceRevisione) => s.aggiorna(d.id, (x) => ({ revisione: [...x.revisione, v] }));
  const vaiA = (x: Intervallo) => {
    setPagina(paginaDi(pagCens, x.inizio));
    setScelto(x);
    setSelezione(null);
    setSalto((n) => n + 1);
  };
  const giaSegnalato = (x: Intervallo) => revisione.some((v) => v.tipo === "sfuggito" && v.inizio === x.inizio && v.fine === x.fine);
  const residuoScelto = scelto?.chiave.startsWith("res:") ? scelto : null;
  const segnaTag = (tipo: TipoVoce) => {
    if (!scelto) return;
    const t = r.tags.find((x) => x.replacement === scelto.chiave && x.start === scelto.inizio) ?? r.tags.find((x) => x.replacement === scelto.chiave)!;
    aggiungi({ tipo, inizio: t.start, fine: t.end, categoria: t.category, sostituto: t.replacement,
               categoria_corretta: tipo === "categoria_errata" ? modulo.categoria || null : undefined, nota: modulo.nota || undefined });
    setModulo({ categoria: "", nota: "" });
  };

  return (
    <section>
      <div className="barra-strumenti">
        <h1 style={{ margin: 0 }}>Confronto</h1>
        <select value={d.id} onChange={(e) => naviga(`/confronto/${e.target.value}`)} aria-label="Documento">
          {pronti.map((x) => <option key={x.id} value={x.id}>{x.nome}</option>)}
        </select>
        <span className="riga">
          <button className="secondario piccolo" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>precedente</button>
          pagina
          <select value={pagina} onChange={(e) => setPagina(Number(e.target.value))} aria-label="Pagina">
            {pagCens.map((p) => <option key={p.numero} value={p.numero}>{p.numero}</option>)}
          </select>
          di {pagCens.length}
          <button className="secondario piccolo" disabled={pagina >= pagCens.length} onClick={() => setPagina(pagina + 1)}>successiva</button>
        </span>
        <label className="inline"><input type="checkbox" checked={resa} onChange={(e) => setResa(e.target.checked)} /> Markdown reso</label>
        <Link to={`/dashboard/${d.id}`}>Dashboard</Link>
      </div>

      <div className="confronto">
        <div className="pannello">
          <div className="pannello-testa">Originale <span className="tenue">{d.nome}</span></div>
          <div className="pannello-corpo">
            {d.file ? <VistaOriginale file={d.file} pagina={pagina} />
              : <p className="tenue">Il file originale non è più disponibile dopo il ricaricamento della pagina (resta solo nella memoria della scheda in cui è stato caricato).</p>}
          </div>
        </div>
        <div className="pannello">
          <div className="pannello-testa">
            In chiaro <span className="tenue">{r.transcript !== undefined ? "trascrizione" : "ripristino con la chiave"}</span>
          </div>
          {chiaro && pk ? (
            <VistaTesto ref={refChiaro} testo={chiaro.text} intervalli={intChiaro} da={pk.inizio} a={pk.fine} resa={resa}
                        scelto={scelto?.chiave ?? null} onScegli={setScelto} onScroll={sincronizza("k")} />
          ) : (
            <div className="pannello-corpo"><p className="tenue">{statoChiaro}</p></div>
          )}
        </div>
        <div className="pannello">
          <div className="pannello-testa">
            Censurato <span className="tenue">{r.tags.length} sostituzioni · {resa ? "per segnalare un valore sfuggito passa alla vista grezza e selezionalo" : "seleziona un testo per segnalarlo"}</span>
          </div>
          {pc && (
            <VistaTesto ref={refCens} testo={r.markdown} intervalli={intCens} da={pc.inizio} a={pc.fine} resa={resa}
                        scelto={scelto?.chiave ?? null} onScegli={(x) => { setScelto(x); setSelezione(null); }}
                        onSeleziona={(i, f) => { setSelezione([i, f]); setScelto(null); }} onScroll={sincronizza("c")} />
          )}
        </div>
      </div>

      {r.report.verification.outcome === "residuals" && (
        <div className="scheda residui" style={{ marginTop: 10 }}>
          <h3 style={{ marginTop: 0 }}>Residui non censurati ({r.report.verification.residuals.length})</h3>
          <p className="nota">
            Valori che la verifica ha trovato ancora in chiaro nel testo censurato, evidenziati con il bordo rosso tratteggiato.
            Segnali come sfuggiti quelli confermati, così finiscono nella revisione esportata.
          </p>
          {residui.length ? (
            <table className="tabella">
              <thead><tr><th>Categoria</th><th className="num">Pagina</th><th>Valore</th><th /></tr></thead>
              <tbody>
                {residui.map((x) => (
                  <tr key={x.chiave} className={x.chiave === scelto?.chiave ? "scelta" : undefined}>
                    <td><span className="pallino" style={{ "--c": colore(x.categoria) } as React.CSSProperties} />{x.categoria}</td>
                    <td className="num">{paginaDi(pagCens, x.inizio)}</td>
                    <td className="mono">{r.markdown.slice(x.inizio, x.fine)}</td>
                    <td>
                      <div className="riga">
                        <button className="secondario piccolo" onClick={() => vaiA(x)}>vai</button>
                        <button className="secondario piccolo" disabled={giaSegnalato(x)}
                                onClick={() => aggiungi({ tipo: "sfuggito", inizio: x.inizio, fine: x.fine, categoria: x.categoria, nota: "residuo della verifica" })}>
                          {giaSegnalato(x) ? "segnalato" : "segna come sfuggito"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <>
              <p className="tenue">Questo report non ha le posizioni dei residui (versione precedente del servizio): solo categoria e pagina.</p>
              <ul>{r.report.verification.residuals.map((x, i) => <li key={i}>{x.category} · pagina {x.page}</li>)}</ul>
            </>
          )}
        </div>
      )}

      <div className="griglia-2" style={{ marginTop: 10 }}>
        <div className="scheda">
          {residuoScelto ? (
            <div className="dettaglio-tag">
              <h3 style={{ marginTop: 0 }}>Residuo segnalato dalla verifica</h3>
              <dl className="coppie">
                <dt>Categoria</dt><dd>{residuoScelto.categoria}</dd>
                <dt>Pagina</dt><dd>{paginaDi(pagCens, residuoScelto.inizio)}</dd>
                <dt>Valore</dt><dd className="mono">{r.markdown.slice(residuoScelto.inizio, residuoScelto.fine)}</dd>
              </dl>
              <p className="nota">Il valore è rimasto in chiaro nel testo censurato: la verifica lo ha trovato ma non è stato corretto.</p>
            </div>
          ) : scelto && !scelto.chiave.startsWith("rev:") ? (
            <div className="dettaglio-tag">
              <h3 style={{ marginTop: 0 }}>
                <mark style={{ "--c": colore(scelto.categoria) } as React.CSSProperties}>{scelto.chiave}</mark>{" "}
                {scelto.categoria}
              </h3>
              <dl className="coppie">
                <dt>Occorrenze</dt><dd>{occorrenze}</dd>
                <dt>Varianti</dt><dd>{entita?.variants ?? "–"}</dd>
                <dt>Trovato da</dt><dd>{entita?.sources.map(nomeFonte).join(", ") ?? "–"}</dd>
                <dt>Valori originali</dt><dd>{chiaro ? originali.join(" · ") || "–" : "imposta la chiave per vederli"}</dd>
              </dl>
              <h3>Revisione</h3>
              <div className="riga">
                <select value={modulo.categoria} onChange={(e) => setModulo({ ...modulo, categoria: e.target.value })} aria-label="Categoria corretta">
                  <option value="">categoria corretta…</option>
                  {categorieTutte.map((c) => <option key={c}>{c}</option>)}
                </select>
                <input placeholder="nota (senza dati personali)" value={modulo.nota} onChange={(e) => setModulo({ ...modulo, nota: e.target.value })} />
              </div>
              <div className="riga" style={{ marginTop: 6 }}>
                <button className="secondario piccolo" onClick={() => segnaTag("censura_errata")}>Censura errata</button>
                <button className="secondario piccolo" disabled={!modulo.categoria} onClick={() => segnaTag("categoria_errata")}>Categoria sbagliata</button>
              </div>
            </div>
          ) : selezione ? (
            <div>
              <h3 style={{ marginTop: 0 }}>Valore sfuggito alla censura</h3>
              <p className="nota">Selezionati {selezione[1] - selezione[0]} caratteri nel censurato.</p>
              <div className="riga">
                <select value={modulo.categoria} onChange={(e) => setModulo({ ...modulo, categoria: e.target.value })} aria-label="Categoria">
                  <option value="">categoria…</option>
                  {categorieTutte.map((c) => <option key={c}>{c}</option>)}
                </select>
                <input placeholder="nota (senza dati personali)" value={modulo.nota} onChange={(e) => setModulo({ ...modulo, nota: e.target.value })} />
                <button className="piccolo" onClick={() => {
                  aggiungi({ tipo: "sfuggito", inizio: selezione[0], fine: selezione[1], categoria: modulo.categoria || null, nota: modulo.nota || undefined });
                  setSelezione(null); setModulo({ categoria: "", nota: "" }); window.getSelection()?.removeAllRanges();
                }}>Segna come sfuggito</button>
              </div>
            </div>
          ) : (
            <p className="tenue">Fai clic su un tag per vederne i dettagli e i valori originali; seleziona un testo nel censurato (vista grezza) per segnalarlo come sfuggito.</p>
          )}
        </div>

        <div className="scheda">
          <div className="riga" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>Revisione ({revisione.length})</h3>
            <button className="piccolo" disabled={!revisione.length}
                    onClick={() => scarica(`${r.report.document.sha256.slice(0, 12)}.revisione.json`,
                                           JSON.stringify(esportaRevisione(revisione, r.report.document.sha256, etichettaModelli(r.report.model), pagCens), null, 2))}>
              Esporta revisione
            </button>
          </div>
          <p className="nota">Il file contiene solo posizioni, lunghezze, categorie, tipo e note: nessun valore del documento.</p>
          {revisione.length > 0 && (
            <table className="tabella">
              <thead><tr><th>Tipo</th><th>Posizione</th><th>Categoria</th><th>Nota</th><th /></tr></thead>
              <tbody>
                {revisione.map((v, i) => (
                  <tr key={i}>
                    <td>{v.tipo.replace("_", " ")}</td>
                    <td className="mono">{v.sostituto ?? `${v.inizio}–${v.fine}`}</td>
                    <td>{v.categoria ?? "–"}{v.categoria_corretta ? ` → ${v.categoria_corretta}` : ""}</td>
                    <td>{v.nota}</td>
                    <td><button className="secondario piccolo" onClick={() => s.aggiorna(d.id, (x) => ({ revisione: x.revisione.filter((_, j) => j !== i) }))}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
