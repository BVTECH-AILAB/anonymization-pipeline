// Grafici della dashboard (Recharts). Colori: slot categoriali della palette di riferimento in ordine fisso
// (--serie-N); una sola serie = un solo colore e niente legenda. Assi e griglia recessivi, tooltip al passaggio.
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const SERIE = ["var(--serie-1)", "var(--serie-2)", "var(--serie-3)", "var(--serie-4)", "var(--serie-5)", "var(--serie-6)"];

const stileTooltip = {
  contentStyle: { background: "var(--superficie)", border: "1px solid var(--bordo)", borderRadius: 6, color: "var(--testo)" },
  labelStyle: { color: "var(--testo)", fontWeight: 600 },
  itemStyle: { color: "var(--testo-2)" },
  cursor: { fill: "var(--superficie-2)" },
};

// legenda: testo nel colore del testo (il quadratino porta il colore della serie), nell'ordine delle serie
const propsLegenda = {
  itemSorter: null,
  wrapperStyle: { fontSize: 12 },
  formatter: (v: unknown) => <span style={{ color: "var(--testo-2)" }}>{String(v)}</span>,
} as const;

/** Vista tabellare dei dati di un grafico (alcuni colori in chiaro sono sotto 3:1 sulla superficie). */
function Tabella({ dati, chiave, serie, unita }: { dati: Record<string, unknown>[]; chiave: string; serie: string[]; unita: string }) {
  return (
    <details className="nota">
      <summary>Dati in tabella</summary>
      <table className="tabella">
        <thead><tr><th>{chiave}</th>{serie.map((s) => <th key={s} className="num">{s}</th>)}</tr></thead>
        <tbody>
          {dati.map((r, i) => (
            <tr key={i}>
              <td>{String(r[chiave])}</td>
              {serie.map((s) => <td key={s} className="num">{typeof r[s] === "number" ? fmt(unita)(r[s]) : "–"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

const fmt = (unita: string) => (v: unknown) => (typeof v === "number" ? `${v.toLocaleString("it-IT", { maximumFractionDigits: 2 })}${unita}` : String(v));

/** Barre orizzontali di una sola serie (es. tempi per fase, conteggi per categoria). */
export function BarreOrizzontali({ dati, unita = "", nome }: { dati: { nome: string; valore: number }[]; unita?: string; nome: string }) {
  const altezza = Math.max(120, dati.length * 26 + 40);
  return (
    <div style={{ width: "100%", height: altezza }}>
      <ResponsiveContainer>
        <BarChart data={dati} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }} barCategoryGap={4}>
          <CartesianGrid horizontal={false} />
          <XAxis type="number" tickFormatter={fmt(unita)} stroke="var(--asse)" />
          <YAxis type="category" dataKey="nome" width={170} stroke="var(--asse)" interval={0} />
          <Tooltip {...stileTooltip} formatter={fmt(unita)} />
          <Bar dataKey="valore" name={nome} fill={SERIE[0]} radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Barre impilate per categoria (es. secondi per pagina divisi per componente). */
export function BarreImpilate({ dati, serie, unita = "" }: { dati: Record<string, string | number>[]; serie: string[]; unita?: string }) {
  return (
    <>
    <div className="grafico">
      <ResponsiveContainer>
        <BarChart data={dati} margin={{ left: 8, right: 16, top: 8, bottom: 4 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="nome" stroke="var(--asse)" />
          <YAxis tickFormatter={fmt(unita)} stroke="var(--asse)" />
          <Tooltip {...stileTooltip} formatter={fmt(unita)} />
          <Legend {...propsLegenda} />
          {serie.map((s, i) => (
            <Bar key={s} dataKey={s} stackId="a" fill={SERIE[i % SERIE.length]} stroke="var(--superficie)" strokeWidth={2}
                 maxBarSize={56} isAnimationActive={false}
                 radius={i === serie.length - 1 ? [4, 4, 0, 0] : undefined} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
    <Tabella dati={dati} chiave="nome" serie={serie} unita={unita} />
    </>
  );
}

/** Barre affiancate di piu' serie (es. storico: secondi per pagina per esecuzione e modello). */
export function BarrePerGruppo({ dati, chiave, gruppi, unita = "" }: { dati: Record<string, string | number | null>[]; chiave: string; gruppi: string[]; unita?: string }) {
  return (
    <>
    <div className="grafico">
      <ResponsiveContainer>
        <BarChart data={dati} margin={{ left: 8, right: 16, top: 8, bottom: 4 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey={chiave} stroke="var(--asse)" />
          <YAxis tickFormatter={fmt(unita)} stroke="var(--asse)" />
          <Tooltip {...stileTooltip} formatter={fmt(unita)} />
          {gruppi.length > 1 && <Legend {...propsLegenda} />}
          {gruppi.map((g, i) => (
            <Bar key={g} dataKey={g} fill={SERIE[i % SERIE.length]} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
    <Tabella dati={dati} chiave={chiave} serie={gruppi} unita={unita} />
    </>
  );
}
