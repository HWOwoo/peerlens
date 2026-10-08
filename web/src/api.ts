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

export type RecentCell = {
  ticker: string;
  metric: string;
  period_type: "TTM" | "Q";
  value: number | null;
  metric_id: string;
  period_end: string;
  period_label: string;
  flags: string[];
  stale: boolean;
};

export type RecentStat = { metric: string; period_type: "TTM" | "Q"; n: number; median: number | null; formula: string };

export type RecentPeriod = {
  ttm: string | null;
  ttm_end: string | null;
  q: string | null;
  q_end: string | null;
  scale: { value: number; unit: string; label: string } | null;
};

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
  recent_cells?: RecentCell[];
  recent_median?: RecentStat[];
  recent_periods?: Record<string, RecentPeriod>;
  stale?: string[];
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
  annual?: boolean;
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
  period_type?: "FY" | "TTM" | "Q";
  period_label?: string;
  explain?: string | null;
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

// ---- Agent ----

export type TraceEvent = {
  seq: number;
  t_ms: number;
  type: "run_start" | "node_start" | "node_end" | "tool" | "tool_result" | "llm" | "result" | "error" | "run_end";
  node: string;
  title: string;
  detail: string;
  data: unknown;
};

export type Segment =
  | { type: "text"; text: string }
  | { type: "metric"; ref_id: string; text: string; label: string; metric_ids: string[]; formula: string };

export type MemoSentence = {
  id: string;
  kind: "quant" | "qual" | "view";
  text: string;
  evidence_ids: string[];
  segments: Segment[];
  status: "pass" | "fail";
  problems: string[];
  verdict: "supported" | "partial" | "unsupported" | null;
  judge_reason: string | null;
  revised?: number;
};

export type MemoEvidence = EvidenceHit & { ref_id: string; question: string };

export type AgentMemo = {
  title: string;
  blocks: { heading: string; sentences: MemoSentence[] }[];
  evidence: MemoEvidence[];
  stats: {
    sentences: number;
    quant: number;
    quant_pass: number;
    qual: number;
    qual_supported: number;
    warnings: number;
    revisions: number;
    metric_refs_used: number;
    evidence_used: number;
  };
};

export type PeerReportRow = {
  ticker: string;
  name?: string;
  include: boolean;
  score: number | null;
  similarity?: number;
  sic?: string;
  sic_match?: string;
  size_ratio?: number | null;
  tool_reason?: string;
  reason: string;
};

export type AgentResult = {
  run_id: string;
  request: string;
  peer_override?: string[] | null;
  parent_run_id?: string | null;
  status: "ok" | "error";
  error?: string;
  target?: string;
  peers?: string[];
  plan?: { memo_angle: string; evidence_questions: { question_ko: string; keywords_en: string }[] };
  peer_report?: PeerReportRow[];
  comparison?: Comparison;
  notes?: string[];
  memo?: AgentMemo;
  analysis?: {
    thesis: string;
    insights: { type: "strength" | "weakness" | "driver" | "risk" | "watch"; claim: string; metric_refs: string[]; evidence_ids: string[]; so_what: string }[];
    peer_contrasts: string[];
    watch_items: string[];
  } | null;
  elapsed_ms: number;
  llm_usage: { calls: number; input_tokens: number; output_tokens: number };
  models: { main: string; fast: string };
  trace: TraceEvent[];
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
  agentRuns: () => request<{ run_id: string; request: string; status: string; elapsed_ms: number; started_at: string }[]>("/api/agent/runs"),
  agentRun: (id: string) => request<AgentResult>(`/api/agent/runs/${encodeURIComponent(id)}`),
  /** Agent 실행을 SSE로 구독. 반환값은 구독 취소 함수. */
  pdfUrl: (runId: string) => `/api/agent/runs/${encodeURIComponent(runId)}/pdf`,
  streamAgent: (
    q: string,
    onEvent: (e: TraceEvent) => void,
    onDone: (r: AgentResult) => void,
    onError: (msg: string) => void,
    opts: { peers?: string[]; parent?: string } = {},
  ) => {
    const qs = new URLSearchParams({ q });
    if (opts.peers?.length) qs.set("peers", opts.peers.join(","));
    if (opts.parent) qs.set("parent", opts.parent);
    const es = new EventSource(`/api/agent/stream?${qs}`);
    let finished = false;
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "final") {
        finished = true;
        es.close();
        onDone(ev.result as AgentResult);
      } else onEvent(ev as TraceEvent);
    };
    es.onerror = () => {
      es.close();
      if (!finished) onError("서버와 연결이 끊겼거나 다른 분석이 실행 중입니다. 잠시 후 다시 시도해 주세요.");
    };
    return () => es.close();
  },
  evidence: (p: { q: string; tickers: string[]; sections: string[]; keywords?: string; k?: number }) => {
    const qs = new URLSearchParams({ q: p.q, k: String(p.k ?? 5) });
    if (p.tickers.length) qs.set("tickers", p.tickers.join(","));
    if (p.sections.length) qs.set("sections", p.sections.join(","));
    if (p.keywords) qs.set("keywords", p.keywords);
    return request<EvidenceResult>(`/api/evidence?${qs}`);
  },
};
