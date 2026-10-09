import "fake-indexeddb/auto";
import { openDB } from "idb";
import { describe, expect, it } from "vitest";
import { dividiPagine, paginaDi } from "../lib/pagine";
import { marca, rehypeMarcatori, segmenti, type Intervallo } from "../lib/evidenzia";
import { etichettaModelli, nomeEsito, nomeFase, nomeFonte, nomeModalita, nomeMotivoRifiuto, nomeMotivoVlm, nomeVerifica, testoAvviso } from "../lib/nomi";
import { esportaRevisione } from "../lib/revisione";
import { alterna, predefiniti, senzaRilevatori } from "../lib/rilevatori";
import * as storico from "../lib/storico";
import type { Report, Status } from "../api/tipi";

const MD = "# Uno\nPaziente: [PERSONA_1]\n\n<!-- pagina 2 -->\n\nFirma: [MEDICO_1] e [PERSONA_1]";

describe("pagine", () => {
  it("divide sui separatori con gli offset nel testo intero", () => {
    const p = dividiPagine(MD);
    expect(p.map((x) => x.numero)).toEqual([1, 2]);
    expect(MD.slice(p[0].inizio, p[0].fine)).toBe("# Uno\nPaziente: [PERSONA_1]");
    expect(MD.slice(p[1].inizio, p[1].fine)).toBe("Firma: [MEDICO_1] e [PERSONA_1]");
    expect(paginaDi(p, MD.indexOf("[MEDICO_1]"))).toBe(2);
    expect(dividiPagine("solo testo")).toEqual([{ numero: 1, inizio: 0, fine: 10 }]);
  });
});

const tag = (s: string, cat: string, da = 0): Intervallo => {
  const i = MD.indexOf(s, da);
  return { inizio: i, fine: i + s.length, chiave: s, categoria: cat };
};

describe("evidenzia", () => {
  const ints = [tag("[PERSONA_1]", "PERSONA"), tag("[MEDICO_1]", "MEDICO")];
  it("segmenti di una pagina", () => {
    const p = dividiPagine(MD)[1];
    const s = segmenti(MD, ints, p.inizio, p.fine);
    expect(s.map((x) => x.testo).join("")).toBe(MD.slice(p.inizio, p.fine));
    expect(s.filter((x) => x.intervallo).map((x) => x.testo)).toEqual(["[MEDICO_1]"]);
    expect(s[0].inizio).toBe(p.inizio);
  });

  it("marcatori trasformati in <mark> anche attraverso nodi diversi", () => {
    const [testo, usati] = marca("ab Mario Rossi cd", [{ inizio: 3, fine: 14, chiave: "[P_1]", categoria: "PERSONA" }]);
    expect(usati).toHaveLength(1);
    // albero hast con il valore spezzato in due nodi di testo (es. enfasi)
    const [prima, dopo] = [testo.slice(0, testo.indexOf("Rossi")), testo.slice(testo.indexOf("Rossi"))];
    const albero = { type: "root", children: [{ type: "text", value: prima }, { type: "element", tagName: "em", children: [{ type: "text", value: dopo }] }] };
    rehypeMarcatori()(albero as never);
    const json = JSON.stringify(albero);
    expect(json).not.toMatch(/[-]/);
    expect((json.match(/"tagName":"mark"/g) ?? []).length).toBe(2);
  });
});

describe("revisione", () => {
  it("esporta posizioni e categorie, mai i valori", () => {
    const f = esportaRevisione(
      [{ tipo: "sfuggito", inizio: 6, fine: 11, categoria: "PERSONA", nota: "nome nel timbro" },
       { tipo: "censura_errata", inizio: 16, fine: 27, categoria: "PERSONA", sostituto: "[PERSONA_1]" }],
      "abc123", "gemma4:e4b-it-qat", dividiPagine(MD),
    );
    expect(f.voci.map((v) => v.lunghezza)).toEqual([5, 11]);
    expect(f.voci[0]).not.toHaveProperty("valore");
    expect(JSON.stringify(f)).not.toContain("Mario");
  });
});

const REPORT = {
  version: "0.1.0", document: { sha256: "f".repeat(64), kind: "pdf", pages: 2 },
  extraction: { docling: [1, 2], vlm: [] }, model: { backend: "ollama", phases: { extraction: "gemma4:26b-a4b-it-qat", detection: "gemma4:e4b-it-qat", redaction: "gemma4:e4b-it-qat", verification: "gemma4:26b-a4b-it-qat" } }, mode: "redaction",
  counts: { PERSONA: 2 }, entities: [], detection: { values: 2, sources: { rules: 1, vlm: 2 }, hallucinations_discarded: 0, inactive_categories: 0, eponyms_discarded: 0 },
  redaction: { coreference: "vlm", groups: 1 }, verification: { mode: "fix", outcome: "skipped", iterations: 1, residuals_fixed: 0, residuals: [] },
  vlm: { detection: { calls: 6, errors: 0, truncated: 0, tokens_in: 100, tokens_out: 10, duration_s: 3 } },
  times_s: { ingest: 0.1, extraction: 2, detection: 5, redaction: 1, verification: 4, total: 12.1 }, warnings: ["verification_skipped"],
} satisfies Report;

describe("storico", () => {
  it("salva, elenca, rinomina, esporta CSV ed elimina", async () => {
    const e = storico.daRisultato(REPORT, null, "prova; con separatore");
    await storico.salva(e);
    await storico.rinomina(e.id, "E4B docling");
    const [x] = await storico.elenco();
    expect(x.etichetta).toBe("E4B docling");
    expect(x.modello).toBe("gemma4:26b-a4b-it-qat + gemma4:e4b-it-qat");
    expect(x.modalita).toBe("redaction");
    expect(x.esito_verifica).toBe("skipped");
    expect(x.totale_s).toBe(12.1);
    expect(x.s_per_pagina).toBeCloseTo(6.05);
    const [colonne, riga] = storico.csv([x]).split("\n").map((r) => r.split(";"));
    const v = Object.fromEntries(colonne.map((c, i) => [c, riga[i]]));
    expect(v).toMatchObject({
      etichetta: "E4B docling", modalita: "redazione", esito_verifica: "non eseguita", totale_s: "12.1",
      acquisizione_s: "0.1", estrazione_s: "2", rilevamento_s: "5", censura_s: "1", verifica_s: "4",
      vlm_chiamate: "6", vlm_token_in: "100", vlm_token_out: "10", valori_regole: "1", valori_vlm: "2", valori_ner: "",
    });
    await storico.elimina(e.id);
    expect(await storico.elenco()).toEqual([]);
  });

  it("non legge le esecuzioni della versione precedente (database con le chiavi in italiano)", async () => {
    const vecchio = await openDB("anonimizzatore-vlm", 1, { upgrade(d) { d.createObjectStore("esecuzioni", { keyPath: "id" }); } });
    await vecchio.put("esecuzioni", { id: "vecchia", salvato: "2026-01-01T00:00:00.000Z", report: { conteggi: {}, tempi_s: {} } });
    vecchio.close();
    expect((await storico.elenco()).map((e) => e.id)).not.toContain("vecchia");
  });
});

describe("nomi", () => {
  it("valori dell'API in italiano, quelli sconosciuti restano com'erano", () => {
    expect(["ingest", "extraction", "detection", "redaction", "verification", "completed"].map(nomeFase))
      .toEqual(["acquisizione", "estrazione", "rilevamento", "censura", "verifica", "completato"]);
    expect(["pseudonym", "tag", "redaction"].map(nomeModalita)).toEqual(["pseudonimo", "tag", "redazione"]);
    expect(["off", "report", "fix", "block"].map(nomeVerifica)).toEqual(["off", "segnala", "correggi", "blocca"]);
    expect(["off", "ok", "skipped", "residuals"].map(nomeEsito)).toEqual(["off", "ok", "non eseguita", "residui"]);
    expect(nomeMotivoRifiuto("verification_unavailable")).toBe("verifica non disponibile");
    expect(nomeMotivoVlm("little_text")).toBe("poco testo");
    expect(["rules", "labels", "detection"].map(nomeFonte)).toEqual(["regole", "etichette", "rilevamento"]);
    expect(nomeFase("nuova_fase")).toBe("nuova_fase");
    expect(nomeFonte("toString")).toBe("toString");
  });

  it("modelli del report: nomi distinti in ordine di fase, anche dai report con un solo modello", () => {
    const stesso = "gemma4:e4b-it-qat";
    expect(etichettaModelli(REPORT.model)).toBe("gemma4:26b-a4b-it-qat + gemma4:e4b-it-qat");
    expect(etichettaModelli({ backend: "ollama", phases: { extraction: stesso, detection: stesso, redaction: stesso, verification: stesso } })).toBe(stesso);
    expect(etichettaModelli({ backend: "ollama", name: stesso })).toBe(stesso); // salvato prima dei modelli per fase
  });

  it("avvisi del report", () => {
    expect(testoAvviso("extraction_truncated:page_3")).toBe("estrazione troncata (pagina 3)");
    expect(testoAvviso("extraction_empty:page_12")).toBe("estrazione vuota (pagina 12)");
    expect(testoAvviso("docling_unavailable")).toBe("docling non disponibile");
    expect(testoAvviso("ner_unavailable")).toBe("ner non disponibile");
    expect(testoAvviso("verification_skipped")).toBe("verifica non eseguita");
    expect(testoAvviso("residuals_not_fixed")).toBe("residui non corretti");
    expect(testoAvviso("altro:valore")).toBe("altro:valore");
  });
});

describe("rilevatori", () => {
  const stato = { detectors: { vlm: true, rules: true, labels: true, eponyms: true, ner: { enabled: false } } } as Status;

  it("partono dai predefiniti del servizio e tornano a null quando coincidono", () => {
    const base = predefiniti(stato);
    expect(base).toEqual(["vlm", "rules", "labels", "eponyms"]);
    const senza_vlm = alterna(null, base, "vlm");
    expect(senza_vlm).toEqual(["rules", "labels", "eponyms"]);
    expect(alterna(senza_vlm, base, "vlm")).toBeNull(); // di nuovo i predefiniti: nessun campo
    expect(alterna(null, base, "ner")).toEqual(["vlm", "rules", "labels", "eponyms", "ner"]);
  });

  it("con lo stato sconosciuto un clic non spegne gli altri rilevatori", () => {
    expect(predefiniti(null)).toBeNull();
    expect(alterna(null, null, "rules")).toBeNull();
    expect(alterna(["ner"], null, "rules")).toEqual(["ner"]);
  });

  it("il filtro eponimi da solo non basta", () => {
    expect(senzaRilevatori(null)).toBe(false);
    expect(senzaRilevatori(["eponyms"])).toBe(true);
    expect(senzaRilevatori(["ner", "eponyms"])).toBe(false);
  });
});
