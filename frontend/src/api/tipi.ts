// Tipi dell'API (anonymization_pipeline, router in api/): nomi e valori in inglese come nel contratto.
// Nessun valore sensibile in report, metriche e tag; trascrizione e occorrenze solo se chieste (IN CHIARO).

export type JobStatus = "queued" | "running" | "completed" | "rejected" | "error" | "cancelled";
export type Phase = "ingest" | "extraction" | "detection" | "redaction" | "verification" | "completed";
/** Rilevatori (vlm = il VLM nella fase di rilevamento) e filtro eponimi, accendibili per richiesta. */
export type Rilevatore = "vlm" | "rules" | "labels" | "ner" | "eponyms";
/** Fasi che chiamano il VLM: ognuna ha il suo modello. */
export type VlmPhase = "extraction" | "detection" | "verification";
export type Mode = "pseudonym" | "tag" | "redaction";
export type VerificationMode = "off" | "report" | "block";
export type ExtractionEngine = "docling" | "vlm";

/** Corpo degli errori: codice stabile, messaggio in italiano (`detail`) e campi in piu' (es. `reason`). */
export interface ApiError {
  code: string;
  detail: string;
  [extra: string]: unknown;
}

export interface Residual {
  category: string;
  page: number;
  // offset nel Markdown censurato, dove il valore e' rimasto in chiaro (assenti nei report meno recenti)
  start?: number;
  end?: number;
}

export interface Job {
  id: string;
  name: string;
  status: JobStatus;
  phase: Phase | null;
  vlm_calls: number;
  created: number;
  started: number | null;
  finished: number | null;
  duration_s: number | null;
  position: number | null;
  expires: number | null;
  reason?: string;
  residuals?: Residual[];
  error?: string;
}

export interface Tag {
  start: number;
  end: number;
  replacement: string;
  category: string | null;
}

export interface Occurrence extends Tag {
  original: string;
  original_start: number;
  original_end: number;
}

export interface Distribution {
  mean: number;
  p50: number;
  p95: number;
  max: number;
}

export interface VlmStats {
  calls: number;
  errors: number;
  retries?: number;
  truncated: number;
  tokens_in: number;
  tokens_out: number;
  duration_s: number;
  latency_s?: Distribution | null;
  queue_wait_s?: Distribution | null;
  tokens_out_per_s?: number | null;
}

export interface Report {
  version: string;
  document: { sha256: string; kind: string; pages: number };
  extraction: { docling: number[]; vlm: { page: number; reason: string }[] };
  model: { backend: string; phases: Record<VlmPhase, string> };
  mode: string;
  counts: Record<string, number>;
  entities: { replacement: string; category: string; variants: number; sources: string[]; occurrences: number | null }[];
  detection: {
    values: number;
    sources: Record<string, number>;
    hallucinations_discarded: number;
    inactive_categories: number;
    eponyms_discarded: number;
  };
  redaction: { groups: number };
  verification: {
    mode: string;
    outcome: string;
    residuals: Residual[];
  };
  layout?: "off" | "pdf" | "unavailable"; // PDF con il layout dell'originale
  vlm: Record<string, VlmStats>;
  times_s: Record<string, number>;
  warnings: string[];
}

export interface Metrics {
  version: string;
  document: { sha256: string; kind: string; pages: number; bytes: number };
  configuration: {
    backend: string;
    models: Record<VlmPhase, { name: string; context: number }>;
    concurrency: number;
    extraction: string;
    detectors: string[];
    eponym_filter: boolean;
    passes: number;
    verification: string;
  };
  times_s: Record<string, number>;
  throughput: { s_per_page: number | null; pages_per_minute: number | null };
  extraction: {
    docling: { pages: number; duration_s: number };
    vlm: { pages: number; duration_s: number; per_page_s: Distribution | null };
    image_encoding_s: number;
  };
  // un oggetto per rilevatore, piu' il numero union_s
  detection: Record<string, { duration_s: number; max_page_s: number; values: number | null } | number>;
  redaction: { replacement_s: number };
  verification: { duration_s: number };
  vlm: Record<string, VlmStats>;
  pages: {
    page: number;
    extracted_with: string | null;
    extraction_s: number | null;
    detection_s: Record<string, number>;
    verification_s: number;
    vlm_calls: number;
  }[];
  resources: { process_cpu_s: number; process_peak_rss_mb: number | null; cpus_available: number | null };
}

export interface Result {
  file_names: Record<string, string>; // markdown, report, map, metrics, transcript, layout_pdf, structure
  markdown: string;
  report: Report;
  metrics: Metrics | null;
  tags: Tag[];
  encrypted_map_b64: string | null;
  layout_pdf_b64?: string | null; // PDF censurato con il layout dell'originale (struttura allegata)
  structure_b64?: string | null; // struttura docling censurata (JSON)
  transcript?: string;
  occurrences?: Occurrence[];
}

export interface RestoreDetail {
  text: string;
  occurrences: Occurrence[];
}

/** GET /readyz: stesso corpo con 200 (pronto) e 503 (non pronto). */
export interface Status {
  status: "ready" | "unavailable";
  version: string;
  backend: { kind: string; reachable: boolean; error: string | null };
  // catalogo del file dei modelli; phases: le fasi di cui e' il predefinito
  models: { name: string; present: boolean | null; phases: VlmPhase[] }[];
  detectors: {
    vlm: boolean;
    rules: boolean;
    labels: boolean;
    eponyms: boolean;
    ner: { enabled: boolean; model?: string; present?: boolean; error?: string | null };
  };
  docling: { enabled: boolean; installed?: boolean; models?: boolean };
  mode: string;
  verification: string;
  active_categories: number;
}

export interface Categories {
  groups: Record<string, string[]>;
  active: string[];
  descriptions: Record<string, string>;
}

/** GET /models: il catalogo (present: nel backend, null se non raggiungibile) e il predefinito di ogni fase. */
export interface Models {
  defaults: Record<VlmPhase, string>;
  models: { name: string; context: number; vision: boolean; present: boolean | null }[];
  error: string | null;
}

/** Opzioni del modulo di caricamento: i campi di POST /jobs. */
export interface Options {
  models: Partial<Record<VlmPhase, string>>; // fase assente o "" = predefinito
  mode: Mode;
  verification: "" | VerificationMode; // "" = predefinita
  extraction: "" | ExtractionEngine; // "" = predefinita
  enable: string[];
  disable: string[];
  transcript: boolean;
  layout: boolean; // anche il PDF con il layout dell'originale
  detectors: Rilevatore[] | null; // insieme completo degli attivi; null = predefiniti del servizio
}
