// Chiave della mappa e "Svuota sessione". La chiave resta in sessionStorage: chiudendo la scheda sparisce.
import { useState } from "react";
import { generaChiave } from "../api/client";
import { useSessione } from "../stato/sessione";

export default function Impostazioni() {
  const s = useSessione();
  const [aperto, setAperto] = useState(false);
  const [mostra, setMostra] = useState(false);
  return (
    <div className="impostazioni">
      <button className="secondario" onClick={() => setAperto((x) => !x)} aria-expanded={aperto}>
        Sessione {s.chiave ? "· chiave impostata" : ""}
      </button>
      {aperto && (
        <div className="pannello-mobile" role="dialog" aria-label="Impostazioni della sessione">
          <label>
            Chiave della mappa (modalità pseudonimo)
            <div className="riga">
              <input type={mostra ? "text" : "password"} value={s.chiave} autoComplete="off"
                     onChange={(e) => s.impostaChiave(e.target.value.trim())} />
              <button className="secondario" onClick={() => setMostra((x) => !x)}>{mostra ? "Nascondi" : "Mostra"}</button>
              <button className="secondario" onClick={() => { s.impostaChiave(generaChiave()); setMostra(true); }}>
                Genera
              </button>
            </div>
          </label>
          <p className="nota">
            La chiave resta solo in questa scheda (sessionStorage). Una chiave generata qui va conservata: senza di
            essa la mappa non si può decifrare.
          </p>
          <button className="pericolo"
                  onClick={async () => { if (confirm("Cancellare lavori, risultati e chiave di questa sessione?")) { await s.svuota(); setAperto(false); } }}>
            Svuota sessione
          </button>
        </div>
      )}
    </div>
  );
}
