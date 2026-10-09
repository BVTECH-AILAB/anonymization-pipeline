// Avviso in alto se un componente del servizio manca (backend, modelli delle fasi, NER, docling).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { salute } from "../api/client";
import type { Status } from "../api/tipi";

export function problemi(s: Status): string[] {
  const out: string[] = [];
  // solo i modelli predefiniti di qualche fase: gli altri del catalogo non servono a essere pronti
  const assenti = s.models.filter((m) => m.phases.length && m.present === false);
  if (!s.backend.reachable) out.push("backend VLM non raggiungibile");
  else if (assenti.length) out.push(...assenti.map((m) => `modello ${m.name} assente`));
  else if (s.status !== "ready") out.push("servizio non pronto");
  if (s.detectors.ner.enabled && s.detectors.ner.present === false)
    out.push("NER non disponibile: rilevamento con meno rilevatori");
  if (s.docling.enabled && !(s.docling.installed && s.docling.models))
    out.push("docling non disponibile: PDF nativi estratti con il VLM");
  for (const v of s.warnings ?? [])
    out.push(`variabile obsoleta ${v} ignorata: i modelli sono nel file dei modelli (ANONPIP_MODELS_FILE)`);
  return out;
}

export default function BannerStato() {
  const [msg, setMsg] = useState<string[]>([]);
  useEffect(() => {
    let vivo = true;
    const leggi = () =>
      salute()
        .then((s) => vivo && setMsg(problemi(s)))
        .catch(() => vivo && setMsg(["API non raggiungibile"]));
    leggi();
    const t = setInterval(leggi, 60000);
    return () => { vivo = false; clearInterval(t); };
  }, []);
  if (!msg.length) return null;
  return (
    <div className="banner" role="status">
      <strong>Attenzione:</strong> {msg.join(" · ")} — <Link to="/stato">dettagli</Link>
    </div>
  );
}
