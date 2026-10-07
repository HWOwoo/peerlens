import { useEffect, useState } from "react";
import { api, type EvidenceHit, type EvidenceResult } from "../api";

const SECTIONS = [
  { key: "", label: "전체" },
  { key: "business", label: "사업 개요" },
  { key: "risk_factors", label: "위험 요인" },
  { key: "mdna", label: "MD&A" },
];

const EXAMPLES = [
  { q: "고객 집중 리스크", kw: "customer concentration significant portion of revenue" },
  { q: "중국 수출 규제가 매출에 미치는 영향", kw: "China export controls license revenue" },
  { q: "AI 데이터센터 수요 전망", kw: "data center AI demand accelerated computing" },
  { q: "파운드리·공급망 의존도", kw: "foundry supply chain third-party manufacturing capacity" },
];

const rankText = (r: number | null) => (r == null ? "–" : `#${r}`);

function Hit({ h }: { h: EvidenceHit }) {
  const [open, setOpen] = useState(false);
  const long = h.text.length > 420;
  return (
    <li className="ev">
      <div className="ev-top">
        <span className="ev-rank num">{h.rank}</span>
        <span className="chip-sm">{h.ticker}</span>
        <span className="ev-meta">
          {h.form} · 공시일 {h.filed} · {h.section_label}
          {h.item && ` (Item ${h.item})`}
        </span>
        <span className="spacer" />
        <span className="ev-score num" title="리랭커 관련도 (0~1)">
          <span className="ev-bar"><i style={{ width: `${Math.round(h.score * 100)}%` }} /></span>
          {h.score.toFixed(2)}
        </span>
      </div>
      {h.subheading && <div className="ev-sub">{h.subheading}</div>}
      <p className="ev-text">{open || !long ? h.text : `${h.text.slice(0, 420)}…`}</p>
      <div className="ev-foot">
        {long && (
          <button className="btn-link" onClick={() => setOpen(!open)}>{open ? "접기" : "전체 문단"}</button>
        )}
        <a href={h.anchor_url} target="_blank" rel="noreferrer">원문 위치로 이동 ↗</a>
        <span className="ev-trace num" title="하이브리드 검색 단계별 순위">
          의미 {rankText(h.dense_rank)} · 키워드 {rankText(h.sparse_rank)} → RRF {rankText(h.fused_rank)} → 리랭크 #{h.rank}
        </span>
      </div>
    </li>
  );
}

/** 공시 원문(10-K·20-F) 근거 검색 — 한국어로 물어도 영어 원문 문단을 찾아 출처·원문 위치와 함께 보여준다. */
export function EvidenceSearch({ tickers }: { tickers: string[] }) {
  const [q, setQ] = useState(EXAMPLES[0].q);
  const [kw, setKw] = useState(EXAMPLES[0].kw);
  const [section, setSection] = useState("");
  const [scope, setScope] = useState<string[]>(tickers);
  const [res, setRes] = useState<EvidenceResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => setScope(tickers), [tickers.join(",")]);

  const run = async (query = q, keywords = kw) => {
    if (query.trim().length < 2) return;
    setLoading(true);
    setErr(null);
    try {
      setRes(await api.evidence({ q: query, tickers: scope, sections: section ? [section] : [], keywords: keywords || undefined }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="card" aria-label="공시 근거 검색">
      <div className="card-head">
        <h2 className="card-title">공시 근거 검색</h2>
        <span className="card-note">최신 10-K·20-F의 사업·위험요인·MD&A 원문에서 근거 문단을 찾습니다 · 한국어 질문 가능</span>
      </div>
      <div className="card-body ev-form">
        <form
          className="ev-inputs"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="예: 고객 집중 리스크" aria-label="검색 질문" />
          <input
            className="input input-sm"
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="키워드(영문, 선택)"
            aria-label="키워드 검색용 영어 질의"
            title="키워드 검색(BM25)은 영어 원문과 단어가 일치해야 합니다. 비워 두면 질문 그대로 사용"
          />
          <button className="btn-primary" disabled={loading}>{loading ? "검색 중…" : "검색"}</button>
        </form>
        <div className="row ev-filters">
          <div className="seg" role="group" aria-label="섹션">
            {SECTIONS.map((s) => (
              <button type="button" key={s.key} aria-pressed={section === s.key} onClick={() => setSection(s.key)}>{s.label}</button>
            ))}
          </div>
          <div className="row">
            {tickers.map((t) => (
              <label key={t} className="check">
                <input
                  type="checkbox"
                  checked={scope.includes(t)}
                  onChange={(e) => setScope(e.target.checked ? [...scope, t] : scope.filter((x) => x !== t))}
                />
                {t}
              </label>
            ))}
          </div>
        </div>
        <div className="row ev-examples">
          <span className="card-note">예시</span>
          {EXAMPLES.map((ex) => (
            <button
              type="button"
              key={ex.q}
              className="example"
              onClick={() => {
                setQ(ex.q);
                setKw(ex.kw);
                run(ex.q, ex.kw);
              }}
            >
              {ex.q}
            </button>
          ))}
        </div>

        {err && <div className="error">⚠ {err}</div>}
        {res && (
          <>
            <div className="ev-pipeline card-note">
              {res.pipeline.embed_model} 의미 검색 + BM25 키워드 검색 → RRF 결합 → {res.pipeline.rerank_model} 재순위 · {res.elapsed_ms.toLocaleString()}ms
            </div>
            {res.results.length === 0 ? (
              <div className="empty">근거 문단을 찾지 못했습니다. 색인되지 않은 기업이거나 질문을 바꿔 보세요.</div>
            ) : (
              <ol className="ev-list">
                {res.results.map((h) => <Hit key={h.chunk_id} h={h} />)}
              </ol>
            )}
          </>
        )}
      </div>
    </section>
  );
}
