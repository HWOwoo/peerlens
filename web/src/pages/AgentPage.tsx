import { useEffect, useRef, useState } from "react";
import { api, type AgentResult, type TraceEvent } from "../api";
import { ComparisonView } from "../components/ComparisonView";
import { EvidencePanel, MemoView } from "../components/MemoView";
import { TraceTimeline } from "../components/TraceTimeline";

const EXAMPLES = [
  "NVIDIA를 반도체 Peer와 비교해서 투자 검토 메모 써줘. 특히 수익성과 중국 리스크가 궁금해.",
  "AMD를 AI 가속기 경쟁사들과 비교해 성장성과 공급망 리스크 중심으로 검토 메모 작성",
  "퀄컴을 Peer와 비교해서 스마트폰 의존도와 수익성 관점의 메모를 써줘",
];

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value num">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function AgentPage({ onOpenMetric, activeMetric }: { onOpenMetric: (id: string) => void; activeMetric: string | null }) {
  const [q, setQ] = useState(EXAMPLES[0]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const cancel = useRef<(() => void) | null>(null);

  // 최근 실행 결과가 있으면 불러와 바로 보여준다 (데모 첫 화면)
  useEffect(() => {
    api.agentRuns().then((runs) => {
      const ok = runs.find((r) => r.status === "ok");
      if (ok) api.agentRun(ok.run_id).then((r) => {
        setResult(r);
        setEvents(r.trace);
        setQ(r.request);
      });
    }).catch(() => undefined);
    return () => cancel.current?.();
  }, []);

  const run = (text = q) => {
    if (text.trim().length < 4 || running) return;
    cancel.current?.();
    setEvents([]);
    setResult(null);
    setErr(null);
    setRunning(true);
    cancel.current = api.streamAgent(
      text,
      (e) => setEvents((xs) => [...xs, e]),
      (r) => {
        setResult(r);
        setRunning(false);
        if (r.status === "error") setErr(r.error ?? "실행 실패");
      },
      (m) => {
        setErr(m);
        setRunning(false);
      },
    );
  };

  const memo = result?.memo;
  const ev = memo?.evidence.find((e) => e.ref_id === evidenceId);

  return (
    <>
      <section className="card agent-input" aria-label="요청">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <label className="field-label" htmlFor="req">무엇을 검토할까요?</label>
          <div className="agent-row">
            <textarea id="req" className="input agent-q" rows={2} value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  run();
                }
              }}
            />
            <button className="btn-primary" disabled={running}>{running ? "분석 중…" : "메모 작성"}</button>
          </div>
        </form>
        <div className="row ev-examples">
          <span className="card-note">예시</span>
          {EXAMPLES.map((x) => (
            <button key={x} type="button" className="example" disabled={running} onClick={() => { setQ(x); run(x); }}>
              {x.length > 34 ? `${x.slice(0, 34)}…` : x}
            </button>
          ))}
        </div>
      </section>

      {err && <div className="error">⚠ {err}</div>}

      {(events.length > 0 || running) && (
        <div className="agent-grid">
          <section className="card trace-card" aria-label="Agent 실행 과정">
            <div className="card-head">
              <h2 className="card-title">실행 과정 (Trace)</h2>
              {result && <span className="card-note num">{(result.elapsed_ms / 1000).toFixed(1)}초 · LLM {result.llm_usage.calls}회</span>}
            </div>
            <div className="card-body">
              <TraceTimeline events={events} running={running} />
            </div>
          </section>

          <div className="agent-main">
            {!memo && running && (
              <section className="card skeleton">계획 → Peer 선정 → 재무 계산 → 공시 근거 검색 → 메모 작성 → 검증 순서로 진행합니다…</section>
            )}
            {memo && result && (
              <>
                <section className="card stats" aria-label="검증 요약">
                  <Stat label="수치 문장 검증" value={`${memo.stats.quant_pass}/${memo.stats.quant}`} sub="모든 수치 = XBRL 계산값" />
                  <Stat label="공시 서술 근거 일치" value={`${memo.stats.qual_supported}/${memo.stats.qual}`} sub="LLM 판정" />
                  <Stat label="재작성" value={`${memo.stats.revisions}회`} sub={memo.stats.warnings ? `⚠ 경고 ${memo.stats.warnings}문장` : "경고 없음"} />
                  <Stat label="인용" value={`${memo.stats.evidence_used}건`} sub={`수치 참조 ${memo.stats.metric_refs_used}개`} />
                </section>
                {result.notes && result.notes.length > 0 && <div className="notice">ⓘ {result.notes.join(" · ")}</div>}
                <section className="card">
                  <div className="card-body">
                    <MemoView memo={memo} onMetric={onOpenMetric} onEvidence={setEvidenceId} />
                  </div>
                </section>
              </>
            )}
          </div>
        </div>
      )}

      {result?.peer_report && (
        <section className="card" aria-label="Peer 선정">
          <div className="card-head">
            <h2 className="card-title">Peer 선정 근거</h2>
            <span className="card-note">도구 점수 = 사업설명 유사도(임베딩) · 업종코드(SIC) · 매출 규모 → LLM이 요청 관점으로 포함·제외 판단</span>
          </div>
          <div className="card-body table-wrap">
            <table className="cmp peer-table">
              <thead>
                <tr>
                  <th>기업</th>
                  <th>선정</th>
                  <th>점수</th>
                  <th>사업설명 유사도</th>
                  <th>업종코드</th>
                  <th style={{ textAlign: "left" }}>판단 이유</th>
                </tr>
              </thead>
              <tbody>
                {result.peer_report.map((p) => (
                  <tr key={p.ticker} className={p.include ? "" : "muted"}>
                    <td><b>{p.ticker}</b> <span className="card-note">{p.name}</span></td>
                    <td>{p.include ? "✓ 포함" : "제외"}</td>
                    <td className="num">{p.score?.toFixed(2) ?? "–"}</td>
                    <td className="num">{p.similarity?.toFixed(2) ?? "–"}</td>
                    <td>{p.sic ? `${p.sic} (${p.sic_match})` : "–"}</td>
                    <td style={{ textAlign: "left", whiteSpace: "normal" }}>{p.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {result?.comparison && <ComparisonView data={result.comparison} onOpenMetric={onOpenMetric} activeMetric={activeMetric} />}

      {ev && <EvidencePanel e={ev} onClose={() => setEvidenceId(null)} />}
    </>
  );
}
