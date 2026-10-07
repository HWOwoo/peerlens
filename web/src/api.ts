export type Company = { ticker: string; name: string; cik: number };

export type CompanyInfo = Company & { currency: string; latest_period_end: string };

export type MetricDef = { name: string; label: string; formula: string };

export type Cell = {
  ticker: string;
  metric: string;
  year: number;
  value: number | null;
  metric_id: string;
  period_end: string;
  fiscal_year: number;
  flags: string[];
};

export type PeerStat = { metric: string; year: number; n: number; median: number | null; formula: string };

export type Comparison = {
  target: string;
  tickers: string[];
  companies: CompanyInfo[];
  align: "calendar" | "fiscal";
  year_label: "CY" | "FY";
  years: number[];
  latest_year: number;
  metrics: MetricDef[];
  cells: Cell[];
  peer_median: PeerStat[];
  missing_latest: string[];
};

export type Fact = {
  fact_id: string;
  ticker: string;
  cik: number;
  company: string;
  concept: string;
  label_ko: string;
  value: number;
  unit: string;
  fiscal_year: number;
  calendar_year: number;
  period_start: string | null;
  period_end: string;
  taxonomy: string;
  tag: string;
  exact_tag: boolean;
  form: string;
  accn: string;
  filed: string;
  filing_url: string;
  api_url: string;
  retrieved_at: string;
  restated: boolean;
  original_value: number | null;
};

export type MetricDetail = {
  metric_id: string;
  ticker: string;
  metric: string;
  label_ko: string;
  value: number | null;
  fiscal_year: number;
  calendar_year: number;
  period_end: string;
  formula: string;
  inputs: string[];
  flags: string[];
  input_facts: Fact[];
};

export type EvidenceHit = {
  rank: number;
  score: number;
  fused_rank: number;
  dense_rank: number | null;
  sparse_rank: number | null;
  chunk_id: string;
  ticker: string;
  company: string;
  form: string;
  accn: string;
  filed: string;
  report_date: string;
  section: string;
  section_label: string;
  item: string;
  subheading: string;
  text: string;
  source_url: string;
  anchor_url: string;
  retrieved_at: string;
};

export type EvidenceResult = {
  query: string;
  keywords: string | null;
  elapsed_ms: number;
  pipeline: { embed_model: string; rerank_model: string | null; fusion: string };
  results: EvidenceHit[];
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `요청 실패 (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  searchCompanies: (q: string) => request<Company[]>(`/api/companies?q=${encodeURIComponent(q)}`),
  compare: (body: { target: string; peers: string[]; years: number; align: "calendar" | "fiscal" }) =>
    request<Comparison>("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  metric: (id: string) => request<MetricDetail>(`/api/metrics/${encodeURIComponent(id)}`),
  evidence: (p: { q: string; tickers: string[]; sections: string[]; keywords?: string; k?: number }) => {
    const qs = new URLSearchParams({ q: p.q, k: String(p.k ?? 5) });
    if (p.tickers.length) qs.set("tickers", p.tickers.join(","));
    if (p.sections.length) qs.set("sections", p.sections.join(","));
    if (p.keywords) qs.set("keywords", p.keywords);
    return request<EvidenceResult>(`/api/evidence?${qs}`);
  },
};
