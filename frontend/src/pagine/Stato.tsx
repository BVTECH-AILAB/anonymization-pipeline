import { useEffect, useState } from "react";
import { salute } from "../api/client";
import type { Status } from "../api/tipi";
import { problemi } from "../componenti/BannerStato";
import { nomeFase, nomeFonte, nomeModalita, nomeVerifica } from "../lib/nomi";

function Voce({ ok, nome, dettaglio }: { ok: boolean | null | undefined; nome: string; dettaglio?: string }) {
  const stato = ok === null || ok === undefined ? "n.d." : ok ? "ok" : "assente";
  return (
    <tr>
      <td>{nome}</td>
      <td><span className={`stato stato-${stato === "ok" ? "ok" : stato === "n.d." ? "nd" : "ko"}`}>{stato}</span></td>
      <td className="tenue">{dettaglio}</td>
    </tr>
  );
}

export default function Stato() {
  const [s, setS] = useState<Status | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const leggi = () => { setErrore(null); salute().then(setS).catch((e) => setErrore(e.message)); };
  useEffect(leggi, []);
  if (errore) return <section><h1>Stato del sistema</h1><p className="errore">API non raggiungibile: {errore}</p></section>;
  if (!s) return <section><h1>Stato del sistema</h1><p>Caricamento…</p></section>;
  const p = problemi(s);
  const r = s.detectors;
  const rilevatori = [...(["vlm", "rules", "labels", "eponyms"] as const).filter((k) => r[k]), ...(r.ner.enabled ? ["ner"] : [])];
  return (
    <section>
      <h1>Stato del sistema</h1>
      <p>{p.length ? <span className="errore">{p.join(" · ")}</span> : <span className="ok">Tutti i componenti sono disponibili.</span>}
        {" "}<button className="secondario" onClick={leggi}>Aggiorna</button></p>
      <table className="tabella">
        <thead><tr><th>Componente</th><th>Stato</th><th>Dettaglio</th></tr></thead>
        <tbody>
          <Voce nome="Backend VLM" ok={s.backend.reachable} dettaglio={`${s.backend.kind}${s.backend.error ? " · " + s.backend.error : ""}`} />
          {s.models.map((m) => (
            <Voce key={m.name} nome="Modello VLM" ok={m.present}
                  dettaglio={`${m.name} · ${m.phases.length ? m.phases.map(nomeFase).join(", ") : "nessuna fase predefinita"}`} />
          ))}
          <Voce nome="NER" ok={r.ner.enabled ? r.ner.present : null} dettaglio={r.ner.enabled ? `${r.ner.model ?? ""}${r.ner.error ? " · " + r.ner.error : ""}` : "disattivato"} />
          <Voce nome="Docling" ok={s.docling.enabled ? !!(s.docling.installed && s.docling.models) : null}
                dettaglio={s.docling.enabled ? `libreria ${s.docling.installed ? "sì" : "no"} · modelli ${s.docling.models ? "sì" : "no"}` : "motore vlm"} />
        </tbody>
      </table>
      <h2>Configurazione</h2>
      <dl className="coppie">
        <dt>Servizio</dt><dd>{s.status === "ready" ? "pronto" : "non pronto"}</dd>
        <dt>Versione</dt><dd>{s.version}</dd>
        <dt>Rilevatori</dt><dd>{rilevatori.map(nomeFonte).join(", ")}</dd>
        <dt>Modalità predefinita</dt><dd>{nomeModalita(s.mode)}</dd>
        <dt>Verifica predefinita</dt><dd>{nomeVerifica(s.verification)}</dd>
        <dt>Categorie attive</dt><dd>{s.active_categories}</dd>
      </dl>
    </section>
  );
}
