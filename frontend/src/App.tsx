import { lazy, Suspense } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import BannerStato from "./componenti/BannerStato";
import Impostazioni from "./componenti/Impostazioni";
import Carica from "./pagine/Carica";
import Stato from "./pagine/Stato";

// pagine con librerie pesanti (pdf.js, grafici) caricate solo quando servono
const Confronto = lazy(() => import("./pagine/Confronto"));
const Dashboard = lazy(() => import("./pagine/Dashboard"));
const Storico = lazy(() => import("./pagine/Storico"));
const Ripristino = lazy(() => import("./pagine/Ripristino"));

const VOCI = [
  ["/carica", "Carica"],
  ["/confronto", "Confronto"],
  ["/dashboard", "Dashboard"],
  ["/storico", "Storico"],
  ["/ripristino", "Ripristino"],
  ["/stato", "Stato"],
] as const;

export default function App() {
  return (
    <div className="app">
      <header className="testata">
        <div className="marchio">
          <span className="marchio-segno" aria-hidden />Anonimizzatore VLM
        </div>
        <nav>
          {VOCI.map(([p, t]) => (
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
          <Route path="*" element={<Navigate to="/carica" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
