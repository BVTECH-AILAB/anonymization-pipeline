// Un testo (Markdown censurato o in chiaro) limitato a una pagina, con gli intervalli evidenziati, in
// vista grezza o resa. Clic su un intervallo: onScegli; selezione nella vista grezza: onSeleziona(inizio, fine)
// con gli offset nel testo intero.
import { forwardRef, useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { colore, marca, rehypeMarcatori, segmenti, type Intervallo } from "../lib/evidenzia";

interface Props {
  testo: string;
  intervalli: Intervallo[];
  da: number;
  a: number;
  resa: boolean;
  scelto: string | null;
  onScegli?: (x: Intervallo) => void;
  onSeleziona?: (inizio: number, fine: number) => void;
  onScroll?: React.UIEventHandler<HTMLDivElement>;
}

const TITOLI: Record<string, string> = { sfuggito: "segnalato come sfuggito", residuo: "residuo della verifica" };

function stileMark(x: Intervallo, scelto: string | null) {
  const segnalato = x.classe === "sfuggito" || x.classe === "residuo";
  return {
    className: [x.chiave === scelto ? "scelto" : "", x.classe ?? ""].join(" ").trim() || undefined,
    style: { "--c": segnalato ? "var(--ko)" : colore(x.categoria) } as React.CSSProperties,
    title: [x.classe ? TITOLI[x.classe] : "", x.categoria ?? ""].filter(Boolean).join(": "),
  };
}

function offsetSelezione(): [number, number] | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  const pos = (n: Node, off: number) => {
    const el = (n.nodeType === Node.TEXT_NODE ? n.parentElement : (n as Element))?.closest("[data-inizio]");
    return el ? Number(el.getAttribute("data-inizio")) + (n.nodeType === Node.TEXT_NODE ? off : 0) : null;
  };
  const i = pos(r.startContainer, r.startOffset);
  const f = pos(r.endContainer, r.endOffset);
  return i === null || f === null || f <= i ? null : [i, f];
}

const VistaTesto = forwardRef<HTMLDivElement, Props>(function VistaTesto(
  { testo, intervalli, da, a, resa, scelto, onScegli, onSeleziona, onScroll }, ref,
) {
  const [marcato, usati] = useMemo(() => marca(testo, intervalli, da, a), [testo, intervalli, da, a]);
  const segs = useMemo(() => (resa ? [] : segmenti(testo, intervalli, da, a)), [resa, testo, intervalli, da, a]);

  const clic = (e: React.MouseEvent) => {
    const m = (e.target as HTMLElement).closest("mark");
    if (!m || !onScegli) return;
    const x = resa ? usati[Number(m.getAttribute("data-i"))] : intervalli[Number(m.getAttribute("data-k"))];
    if (x) onScegli(x);
  };

  return (
    <div className="pannello-corpo" ref={ref} onScroll={onScroll} onClick={clic}
         onMouseUp={() => { const s = !resa && offsetSelezione(); if (s && onSeleziona) onSeleziona(s[0], s[1]); }}>
      {resa ? (
        <div className="md">
          <Markdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeMarcatori]}
            components={{
              mark: ({ node: _n, ...p }) => {
                const x = usati[Number((p as Record<string, unknown>)["data-i"])];
                return x ? <mark {...p} {...stileMark(x, scelto)} /> : <mark {...p} />;
              },
            }}
          >
            {marcato}
          </Markdown>
        </div>
      ) : (
        <pre className="grezzo">
          {segs.map((s, k) =>
            s.intervallo ? (
              <mark key={k} data-inizio={s.inizio} data-k={intervalli.indexOf(s.intervallo)} {...stileMark(s.intervallo, scelto)}>
                {s.testo}
              </mark>
            ) : (
              <span key={k} data-inizio={s.inizio}>{s.testo}</span>
            ),
          )}
        </pre>
      )}
    </div>
  );
});

export default VistaTesto;
