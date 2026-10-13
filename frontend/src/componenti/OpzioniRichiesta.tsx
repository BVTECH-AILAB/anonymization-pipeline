// Opzioni di POST /jobs in sezioni richiudibili (output, elaborazione, rilevatori, categorie), ognuna con
// il riassunto delle scelte nell'intestazione. Le categorie si scelgono per gruppo o una per una.
import { useState } from "react";
import type { Categories, Models, Options, Status } from "../api/tipi";
import { alternaCategoria, alternaGruppo, modifiche, numeroModifiche, scelte, statoGruppo } from "../lib/categorie";
import { FASI_VLM, nomeFase, nomeModalita, nomeVerifica } from "../lib/nomi";
import { alterna as alternaRilevatore, predefiniti, RILEVATORI } from "../lib/rilevatori";

interface Props {
  o: Options;
  setO: React.Dispatch<React.SetStateAction<Options>>;
  predefinite: Options;
  cat: Categories | null;
  mod: Models | null;
  stato: Status | null;
  leggiModelli: () => void;
  riprovaStato: () => void;
  titolo?: string;
  senzaTrascrizione?: boolean; // validazione: la trascrizione la chiede sempre, non si sceglie
}

function Sezione({ titolo, riassunto, aperta = false, children }: { titolo: string; riassunto: string; aperta?: boolean; children: React.ReactNode }) {
  return (
    <details className="sezione" open={aperta}>
      <summary>
        <span className="sezione-titolo">{titolo}</span>
        <span className="tenue sezione-riassunto">{riassunto}</span>
      </summary>
      <div className="sezione-corpo">{children}</div>
    </details>
  );
}

function Categorie({ o, setO, cat }: { o: Options; setO: Props["setO"]; cat: Categories }) {
  const [filtro, setFiltro] = useState("");
  const s = scelte(cat, o);
  const q = filtro.trim().toLowerCase();
  const corrisponde = (c: string) => !q || c.toLowerCase().includes(q) || (cat.descriptions[c] ?? "").toLowerCase().includes(q);
  const applica = (m: Pick<Options, "enable" | "disable">) => setO((x) => ({ ...x, ...m }));
  return (
    <>
      <div className="riga">
        <input type="search" placeholder="cerca una categoria" value={filtro} onChange={(e) => setFiltro(e.target.value)} aria-label="Cerca una categoria" />
        <button className="secondario piccolo" onClick={() => applica(modifiche(cat, new Set(Object.values(cat.groups).flat())))}>tutte</button>
        <button className="secondario piccolo" onClick={() => applica({ enable: [], disable: [] })}>predefinite</button>
      </div>
      <div className="gruppi">
        {Object.entries(cat.groups).map(([g, cs]) => {
          const visibili = g.includes(q) ? cs : cs.filter(corrisponde);
          if (!visibili.length) return null;
          const st = statoGruppo(cat, s, g);
          return (
            <details key={g} className="gruppo" open={q ? true : undefined}>
              <summary>
                <input type="checkbox" checked={st === "tutte"} aria-label={`gruppo ${g}`}
                       ref={(el) => { if (el) el.indeterminate = st === "alcune"; }}
                       onChange={() => applica(alternaGruppo(cat, o, g))} />
                <span>{g}</span>
                <span className="tenue">({cs.filter((c) => s.has(c)).length}/{cs.length})</span>
              </summary>
              <div className="griglia-categorie">
                {visibili.map((c) => (
                  <label key={c} className="inline" title={cat.descriptions[c]}>
                    <input type="checkbox" checked={s.has(c)} onChange={() => applica(alternaCategoria(cat, o, c))} /> {c}
                  </label>
                ))}
              </div>
            </details>
          );
        })}
      </div>
    </>
  );
}

export default function OpzioniRichiesta({ o, setO, predefinite, cat, mod, stato, leggiModelli, riprovaStato, titolo = "Opzioni della richiesta", senzaTrascrizione = false }: Props) {
  const base = predefiniti(stato);
  const attivi = o.detectors ?? base ?? [];
  const modelli = FASI_VLM.filter((f) => o.models[f]).length;
  const tutte = cat ? Object.values(cat.groups).flat() : [];
  const accese = cat ? tutte.filter((c) => scelte(cat, o).has(c)).length : 0;
  const nCat = cat ? numeroModifiche(cat, o) : 0;

  return (
    <div className="scheda">
      <div className="riga" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{titolo}</h2>
        <button className="secondario piccolo" onClick={() => setO(predefinite)}>ripristina predefinite</button>
      </div>

      <Sezione titolo="Output" aperta
               riassunto={[nomeModalita(o.mode), o.layout && "PDF con layout", !senzaTrascrizione && o.transcript && "trascrizione in chiaro"].filter(Boolean).join(" · ")}>
        <div className="griglia-opzioni">
          <label>
            Modalità
            <select value={o.mode} onChange={(e) => setO({ ...o, mode: e.target.value as Options["mode"] })}>
              <option value="pseudonym">pseudonimo (tag + mappa cifrata)</option>
              <option value="tag">tag</option>
              <option value="redaction">redazione</option>
            </select>
          </label>
        </div>
        <label className="inline">
          <input type="checkbox" checked={o.layout} onChange={(e) => setO({ ...o, layout: e.target.checked })} />
          Produci anche il PDF censurato con il layout dell'originale
        </label>
        {!senzaTrascrizione && (
          <label className="inline">
            <input type="checkbox" checked={o.transcript} onChange={(e) => setO({ ...o, transcript: e.target.checked })} />
            Restituisci anche la trascrizione in chiaro (debug)
          </label>
        )}
        {o.transcript && !senzaTrascrizione && (
          <p className="avviso-rosso">
            Il testo IN CHIARO del documento viaggia nella risposta e resta in memoria sul server fino alla scadenza del
            lavoro. Usalo solo per prove; con la modalità pseudonimo il confronto funziona anche senza, con la chiave.
          </p>
        )}
      </Sezione>

      <Sezione titolo="Elaborazione"
               riassunto={`verifica ${o.verification ? nomeVerifica(o.verification) : "predefinita"} · estrazione ${o.extraction || "predefinita"}${modelli ? ` · ${modelli} modelli scelti` : ""}`}>
        <div className="griglia-opzioni">
          <label>
            Verifica
            <select value={o.verification} onChange={(e) => setO({ ...o, verification: e.target.value as Options["verification"] })}>
              <option value="">predefinita</option>
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
                  // l'estrazione legge le pagine scansionate: solo modelli con visione
                  <option key={m.name} value={m.name} disabled={m.present !== true || (f === "extraction" && !m.vision)}>
                    {m.name}{m.vision ? "" : " (solo testo)"}{m.present === false ? " (assente nel backend)" : ""}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {mod?.error && <p className="nota errore">elenco dei modelli non disponibile: {mod.error}</p>}
      </Sezione>

      <Sezione titolo="Rilevatori"
               riassunto={o.detectors === null ? "predefiniti del servizio" : `${attivi.length}/${RILEVATORI.length} attivi`}>
        {base && (
          <div className="griglia-opzioni">
            {RILEVATORI.map(({ r, nome, nota }) => (
              <label key={r} className="inline" title={nota}>
                <input type="checkbox" checked={attivi.includes(r)}
                       onChange={() => setO((x) => ({ ...x, detectors: alternaRilevatore(x.detectors, base, r) }))} /> {nome}
                {r === "ner" && stato?.detectors.ner.present === false && (
                  <span className="nota errore" title={stato.detectors.ner.error ?? undefined}> (non disponibile)</span>
                )}
              </label>
            ))}
          </div>
        )}
        {!stato && (
          <p className="nota">
            Stato del servizio non disponibile: i rilevatori restano quelli predefiniti.{" "}
            <button className="secondario piccolo" onClick={riprovaStato}>riprova</button>
          </p>
        )}
      </Sezione>

      {cat && (
        <Sezione titolo="Categorie" aperta
                 riassunto={`${accese}/${tutte.length} attive${nCat ? ` · ${nCat} ${nCat === 1 ? "modifica" : "modifiche"}` : ""}`}>
          <Categorie o={o} setO={setO} cat={cat} />
        </Sezione>
      )}
    </div>
  );
}
