import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/client";
import type { Status } from "../api/tipi";
import { problemi } from "../componenti/BannerStato";

afterEach(() => { vi.restoreAllMocks(); });

/** fetch finto: una risposta nuova a ogni chiamata (il corpo si legge una volta sola). */
function finto(stato: number, corpo: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(corpo), { status: stato, headers: { "Content-Type": "application/json" } }));
}

const chiamata = (f: ReturnType<typeof finto>, i = 0) => f.mock.calls[i] as [string, RequestInit | undefined];

const STATO: Status = {
  status: "unavailable",
  version: "0.1.0",
  backend: { kind: "ollama", reachable: true, error: null },
  models: [
    { name: "vlm-grande:1", present: false, phases: ["extraction", "detection", "verification"] },
    { name: "vlm-piccolo:1", present: false, phases: [] }, // nel catalogo, di nessuna fase
  ],
  detectors: { vlm: true, rules: true, labels: true, eponyms: true, ner: { enabled: true, model: "ner-it", present: false, error: "modello assente" } },
  docling: { enabled: true, installed: true, models: false },
  mode: "pseudonym",
  verification: "block",
  active_categories: 30,
};

const presenti = (present: boolean | null): Status["models"] => STATO.models.map((m) => ({ ...m, present }));

describe("client API", () => {
  it("accoda un lavoro con le opzioni, senza header di autenticazione", async () => {
    const f = finto(202, { id: "x", status: "queued" });
    const file = new File(["ciao"], "nota.txt");
    await api.nuovoLavoro(file, { models: { detection: "vlm-piccolo:1", verification: "" }, mode: "pseudonym", verification: "", extraction: "vlm", enable: [], disable: ["sanitari", "DATA_NASCITA"], transcript: true, layout: true, detectors: ["rules", "ner"] }, "K");
    const [url, init] = chiamata(f);
    expect(url).toBe("/api/jobs");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
    const body = init?.body as FormData;
    expect(body.get("mode")).toBe("pseudonym");
    expect(body.get("extraction")).toBe("vlm");
    expect(body.get("model_detection")).toBe("vlm-piccolo:1");
    for (const campo of ["model", "model_extraction", "model_verification"]) expect(body.has(campo)).toBe(false);
    expect(body.get("disable")).toBe("sanitari,DATA_NASCITA"); // gruppi e categorie singole
    expect(body.get("detectors")).toBe("rules,ner");
    expect(body.has("enable")).toBe(false);
    expect(body.get("transcript")).toBe("true");
    expect(body.get("layout")).toBe("true");
    expect(body.get("key")).toBe("K");
    expect(body.has("verification")).toBe(false);
  });

  it("la chiave parte solo in modalità pseudonimo", async () => {
    const f = finto(202, { id: "x", status: "queued" });
    await api.nuovoLavoro(new File(["ciao"], "nota.txt"), { models: {}, mode: "tag", verification: "block", extraction: "", enable: ["date"], disable: [], transcript: false, layout: false, detectors: null }, "K");
    const body = chiamata(f)[1]?.body as FormData;
    expect(body.get("mode")).toBe("tag");
    expect(body.get("verification")).toBe("block");
    expect(body.get("enable")).toBe("date");
    for (const campo of ["key", "model_detection", "extraction", "transcript", "layout", "disable", "detectors"]) expect(body.has(campo)).toBe(false);
  });

  it("percorsi di lavori, risultato, catalogo e ripristino", async () => {
    const f = finto(200, {});
    await api.lavoro("a/b");
    await api.risultato("x");
    await api.risultatoZip("x");
    await api.categorie();
    await api.modelli();
    await api.ripristinaDettaglio("# testo", new Blob(["mappa"]), "K");
    await api.ripristinaPdf(new File(["%PDF"], "a.anon.pdf"), new Blob(["mappa"]), "K");
    expect(f.mock.calls.map((c) => c[0])).toEqual([
      "/api/jobs/a%2Fb", "/api/jobs/x/result", "/api/jobs/x/result?format=zip", "/api/categories", "/api/models", "/api/restore/detail",
      "/api/restore/pdf",
    ]);
    expect([...(chiamata(f, 6)[1]?.body as FormData).keys()]).toEqual(["document", "map", "key"]);
    const body = chiamata(f, 5)[1]?.body as FormData;
    expect([...body.keys()]).toEqual(["markdown", "map", "key"]);
    expect(body.get("key")).toBe("K");
  });

  it("cancella un lavoro (204 senza corpo)", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(null, { status: 204 }));
    await api.cancellaLavoro("x");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/jobs/x");
    expect(init.method).toBe("DELETE");
  });

  it("errori con il dettaglio e il codice dell'API", async () => {
    finto(422, { code: "UNREDACTABLE_CONTENT", detail: "Il documento non può essere anonimizzato in modo sicuro.", reason: "residuals", residuals: [] });
    await expect(api.risultato("x")).rejects.toMatchObject({
      stato: 422,
      message: "Il documento non può essere anonimizzato in modo sicuro.",
      corpo: { code: "UNREDACTABLE_CONTENT", reason: "residuals" },
    });
  });

  it("errore senza corpo JSON (es. dal proxy)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("<html></html>", { status: 502, statusText: "Bad Gateway" }));
    await expect(api.categorie()).rejects.toMatchObject({ stato: 502, message: "502 Bad Gateway", corpo: null });
  });

  it("/readyz: servizio pronto", async () => {
    const f = finto(200, { ...STATO, status: "ready", models: presenti(true) });
    expect((await api.salute()).status).toBe("ready");
    expect(chiamata(f)[0]).toBe("/api/readyz");
  });

  it("/readyz: il 503 con lo stato non è un errore", async () => {
    finto(503, STATO);
    const s = await api.salute();
    expect(s.status).toBe("unavailable");
    expect(problemi(s)).toEqual([
      "modello vlm-grande:1 assente",
      "NER non disponibile: rilevamento con meno rilevatori",
      "docling non disponibile: PDF nativi estratti con il VLM",
    ]);
  });

  it("/readyz: un 503 senza lo stato è un errore", async () => {
    finto(503, { code: "SERVICE_UNAVAILABLE", detail: "Servizio temporaneamente non disponibile. Riprova più tardi." });
    await expect(api.salute()).rejects.toMatchObject({ stato: 503, message: "Servizio temporaneamente non disponibile. Riprova più tardi." });
  });

  it("chiave generata nel formato del servizio (32 byte in base64 url-safe)", () => {
    const k = api.generaChiave();
    expect(k).toHaveLength(44);
    expect(k).toMatch(/^[A-Za-z0-9_-]+=$/);
  });
});

describe("stato del servizio", () => {
  it("backend non raggiungibile, servizio non pronto, componenti spenti", () => {
    const giu: Status = { ...STATO, backend: { ...STATO.backend, reachable: false, error: "ConnectError" }, models: presenti(null) };
    expect(problemi(giu)[0]).toBe("backend VLM non raggiungibile");
    expect(problemi({ ...STATO, models: presenti(true) })[0]).toBe("servizio non pronto");
    // un modello del catalogo che nessuna fase usa non e' un problema
    const spenti: Status = { ...STATO, status: "ready", models: [{ ...STATO.models[0], present: true }, STATO.models[1]], detectors: { ...STATO.detectors, ner: { enabled: false } }, docling: { enabled: false } };
    expect(problemi(spenti)).toEqual([]);
  });
});
