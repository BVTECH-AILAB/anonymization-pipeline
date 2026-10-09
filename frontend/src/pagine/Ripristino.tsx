// Ripristino dei valori originali: Markdown pseudonimizzato + mappa cifrata + chiave.
import { useState } from "react";
import { ripristinaDettaglio } from "../api/client";
import type { RestoreDetail } from "../api/tipi";
import VistaTesto from "../componenti/VistaTesto";
import { useSessione } from "../stato/sessione";

export default function Ripristino() {
  const s = useSessione();
  const [md, setMd] = useState<File | null>(null);
  const [mappa, setMappa] = useState<File | null>(null);
  const [chiave, setChiave] = useState("");
  const [esito, setEsito] = useState<RestoreDetail | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [resa, setResa] = useState(false);

  const k = chiave || s.chiave;
  const esegui = async () => {
    setErrore(null); setEsito(null);
    try {
      setEsito(await ripristinaDettaglio(await md!.text(), mappa!, k));
    } catch (e) {
      setErrore((e as Error).message);
    }
  };
  const scarica = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([esito!.text], { type: "text/markdown" }));
    a.download = (md?.name ?? "documento.anon.md").replace(/\.anon\.md$/, "") + ".ripristinato.md";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section>
      <h1>Ripristino</h1>
      <div className="scheda griglia-opzioni">
        <label>Markdown censurato (.anon.md)<input type="file" accept=".md" onChange={(e) => setMd(e.target.files?.[0] ?? null)} /></label>
        <label>Mappa cifrata (.mappa.enc)<input type="file" accept=".enc" onChange={(e) => setMappa(e.target.files?.[0] ?? null)} /></label>
        <label>
          Chiave {s.chiave && !chiave ? <span className="nota">(quella della sessione)</span> : null}
          <input type="password" value={chiave} onChange={(e) => setChiave(e.target.value.trim())} autoComplete="off" placeholder={s.chiave ? "••••" : ""} />
        </label>
        <div className="riga" style={{ alignSelf: "end" }}>
          <button disabled={!md || !mappa || !k} onClick={esegui}>Ripristina</button>
        </div>
      </div>
      {errore && <p className="errore">{errore}</p>}
      {esito && (
        <>
          <div className="barra-strumenti" style={{ marginTop: 14 }}>
            <strong>{esito.occurrences.length} valori ripristinati</strong>
            <label className="inline"><input type="checkbox" checked={resa} onChange={(e) => setResa(e.target.checked)} /> Markdown reso</label>
            <button className="secondario" onClick={scarica}>Scarica il testo</button>
            <button className="secondario" onClick={() => setEsito(null)}>Chiudi</button>
          </div>
          <p className="avviso-rosso">Testo IN CHIARO: non viene salvato, resta solo in questa pagina finché non la chiudi.</p>
          <div className="pannello" style={{ height: "60vh" }}>
            <VistaTesto testo={esito.text} da={0} a={esito.text.length} resa={resa} scelto={null}
                        intervalli={esito.occurrences.map((o) => ({ inizio: o.original_start, fine: o.original_end, chiave: o.replacement, categoria: o.category }))} />
          </div>
        </>
      )}
    </section>
  );
}
