import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { disponibile } from "./api/validazione";
import BannerStato from "./componenti/BannerStato";
import Impostazioni from "./componenti/Impostazioni";
import Carica from "./pagine/Carica";
import Stato from "./pagine/Stato";

// pagine con librerie pesanti (pdf.js, grafici) caricate solo quando servono
const Confronto = lazy(() => import("./pagine/Confronto"));
const Dashboard = lazy(() => import("./pagine/Dashboard"));
const Storico = lazy(() => import("./pagine/Storico"));
const Ripristino = lazy(() => import("./pagine/Ripristino"));
const Validazione = lazy(() => import("./pagine/Validazione"));

const VOCI = [
  ["/carica", "Carica"],
  ["/confronto", "Confronto"],
  ["/dashboard", "Dashboard"],
  ["/storico", "Storico"],
  ["/ripristino", "Ripristino"],
  ["/stato", "Stato"],
] as const;

export default function App() {
  // la validazione esiste solo nello stack locale (servizio validation di docker compose)
  const [validazione, setValidazione] = useState(false);
  useEffect(() => { disponibile().then(setValidazione); }, []);
  const voci = validazione ? [...VOCI.slice(0, -1), ["/validazione", "Validazione"] as const, VOCI[VOCI.length - 1]] : VOCI;
  return (
    <div className="app">
      <header className="testata">
        <div className="marchio">
          <span className="marchio-segno" aria-hidden />Anonimizzatore VLM
        </div>
        <nav>
          {voci.map(([p, t]) => (
            <NavLink key={p} to={p} className={({ isActive }) => (isActive ? "attivo" : "")}>
              {t}
            </NavLink>
          ))}
        </nav>
        <Impostazioni />
      </header>
      <BannerStato />
      <main>
        <Suspense fallback={<p className="tenue">Caricamento…</p>}>
        <Routes>
          <Route path="/" element={<Navigate to="/carica" replace />} />
          <Route path="/carica" element={<Carica />} />
          <Route path="/confronto/:id?" element={<Confronto />} />
          <Route path="/dashboard/:id?" element={<Dashboard />} />
          <Route path="/storico" element={<Storico />} />
          <Route path="/ripristino" element={<Ripristino />} />
          <Route path="/stato" element={<Stato />} />
          <Route path="/validazione/:id?" element={<Validazione />} />
          <Route path="*" element={<Navigate to="/carica" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
