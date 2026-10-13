import { afterEach, describe, expect, it, vi } from "vitest";
import * as v from "../api/validazione";
import type { ConfigValidazione, DocumentoDataset } from "../api/validazione";
import { aProfilo, daInviare, daParametri, nuovoNome, problemi, profiliUI, selezionati, type ProfiloUI } from "../lib/validazione";

afterEach(() => { vi.restoreAllMocks(); });

function finto(stato: number, corpo: unknown, tipo = "application/json") {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(typeof corpo === "string" ? corpo : JSON.stringify(corpo), { status: stato, headers: { "Content-Type": tipo } }));
}

const chiamata = (f: ReturnType<typeof finto>, i = 0) => f.mock.calls[i] as [string, RequestInit | undefined];

const CONFIG: ConfigValidazione = {
  server: { base_url: "http://localhost:8000", poll_interval_s: 2, timeout_documento_s: 1800, concorrenza: 1, chiave_env: "ANONVAL_KEY" },
  dataset: { percorso: "/x/dataset", tipi: [], difficolta: [], documenti: [] },
  soglie: { recall_min: 0.95, tasso_fuga_max: 0, precision_min: 0.8, f1_min: 0.85 },
  comune: { mode: "tag", verification: "report", extraction: "docling", layout: false, disable: ["date", "sanitari"] },
  profili: [
    { nome: "default", descrizione: "Default" },
    { nome: "noner", descrizione: "", detectors: ["vlm", "rules", "labels", "eponyms"], model_detection: "m:2" },
  ],
};

const DOCS: DocumentoDataset[] = [
  { id: "referto_laboratorio_facile_01", type: "referto_laboratorio", difficulty: "facile", pages: 1, entities: 20 },
  { id: "referto_laboratorio_media_01", type: "referto_laboratorio", difficulty: "media", pages: 2, entities: 40 },
  { id: "test_genetico_complessa_01", type: "test_genetico", difficulty: "complessa", pages: 4, entities: 90 },
];

describe("validazione: profili", () => {
  it("fonde comune e profilo nelle opzioni del modulo", () => {
    const [def, noner] = profiliUI(CONFIG, "pseudonym");
    expect(def.o).toMatchObject({ mode: "tag", verification: "report", extraction: "docling", disable: ["date", "sanitari"], detectors: null, transcript: true });
    expect(noner.o.detectors).toEqual(["vlm", "rules", "labels", "eponyms"]);
    expect(noner.o.models).toEqual({ detection: "m:2" });
    expect(daParametri({}, {}, "pseudonym").mode).toBe("pseudonym");
  });

  it("riporta le opzioni a un profilo completo, campi vuoti = default del servizio", () => {
    const [, noner] = profiliUI(CONFIG, "tag");
    expect(aProfilo(noner)).toEqual({
      nome: "noner", descrizione: "", mode: "tag", verification: "report", extraction: "docling",
      enable: null, disable: ["date", "sanitari"], detectors: ["vlm", "rules", "labels", "eponyms"], layout: false,
      model_extraction: null, model_detection: "m:2", model_verification: null,
    });
  });

  it("configurazione da inviare: profili completi, comune vuoto, filtri e soglie del modulo", () => {
    const profili = profiliUI(CONFIG, "tag");
    const c = daInviare(CONFIG, profili, { tipi: ["test_genetico"], difficolta: [], documenti: [] },
      { ...CONFIG.soglie, tasso_fuga_max: 0.05 }, { concorrenza: 2, timeout_documento_s: 600 });
    expect(c.comune).toEqual({});
    expect(c.profili.map((p) => p.nome)).toEqual(["default", "noner"]);
    expect(c.profili[0].disable).toEqual(["date", "sanitari"]);
    expect(c.dataset).toMatchObject({ percorso: "/x/dataset", tipi: ["test_genetico"] });
    expect(c.server).toMatchObject({ concorrenza: 2, timeout_documento_s: 600, base_url: "http://localhost:8000" });
    expect(c.soglie.tasso_fuga_max).toBe(0.05);
  });

  it("segnala nomi non validi o ripetuti, rilevatori assenti, chiave mancante", () => {
    const [a, b] = profiliUI(CONFIG, "tag");
    const doppio: ProfiloUI = { ...b, nome: "default" };
    const senza: ProfiloUI = { ...a, nome: "Con Spazi", o: { ...a.o, detectors: ["eponyms"] } };
    expect(problemi([a, doppio], 1, false)).toEqual(["i nomi dei profili devono essere diversi"]);
    expect(problemi([senza], 0, true)).toEqual([
      "nessun documento corrisponde ai filtri",
      "profilo 1: nome non valido (minuscole, cifre, - e _)",
      "Con Spazi: serve almeno un rilevatore oltre al filtro eponimi",
      "modalita' pseudonimo: serve la chiave",
    ]);
    expect(problemi([], 1, false)).toEqual(["serve almeno un profilo"]);
    expect(nuovoNome([a, { ...b, nome: "profilo-3" }])).toBe("profilo-4");
  });
});

describe("validazione: dataset", () => {
  it("filtra come il backend", () => {
    expect(selezionati(DOCS, { tipi: [], difficolta: [], documenti: [] })).toHaveLength(3);
    expect(selezionati(DOCS, { tipi: ["referto_laboratorio"], difficolta: ["media"], documenti: [] }).map((d) => d.id))
      .toEqual(["referto_laboratorio_media_01"]);
    expect(selezionati(DOCS, { tipi: [], difficolta: [], documenti: ["test_genetico_complessa_01"] })).toHaveLength(1);
  });
});

describe("validazione: client", () => {
  it("disponibile solo con il JSON di /health (nel cluster arriva index.html)", async () => {
    finto(200, { status: "ok" });
    expect(await v.disponibile()).toBe(true);
    vi.restoreAllMocks();
    finto(200, "<!doctype html><html></html>", "text/html");
    expect(await v.disponibile()).toBe(false);
  });

  it("avvia una run con configurazione e chiave nel corpo JSON", async () => {
    const f = finto(202, { id: "20261013-101500" });
    expect(await v.avvia(CONFIG, "segreta")).toEqual({ id: "20261013-101500" });
    const [url, init] = chiamata(f);
    expect(url).toBe("/validazione-api/runs");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ config: CONFIG, key: "segreta" });
  });

  it("errori: detail testuale o lista degli errori dei campi (422)", async () => {
    finto(409, { detail: "Una validazione e' gia' in corso." });
    await expect(v.avvia(CONFIG, null)).rejects.toThrow("gia' in corso");
    vi.restoreAllMocks();
    finto(422, { detail: [{ loc: ["body", "config", "profili", 0, "mode"], msg: "Input should be 'tag'" }] });
    await expect(v.avvia(CONFIG, null)).rejects.toThrow("config.profili.0.mode: Input should be 'tag'");
  });

  it("percorsi delle run", async () => {
    const f = finto(200, {});
    await v.run("20261013-101500");
    await v.annulla("20261013-101500");
    await v.riprendi("20261013-101500", null);
    expect(chiamata(f, 0)[0]).toBe("/validazione-api/runs/20261013-101500");
    expect(chiamata(f, 1)).toEqual(["/validazione-api/runs/20261013-101500/cancel", { method: "POST" }]);
    expect(chiamata(f, 2)[0]).toBe("/validazione-api/runs/20261013-101500/resume");
  });
});
