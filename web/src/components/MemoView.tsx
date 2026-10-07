import type { AgentMemo, MemoEvidence, MemoSentence } from "../api";

const KIND_LABEL = { quant: "수치", qual: "공시", view: "의견" } as const;
const VERDICT_LABEL = { supported: "근거 일치", partial: "부분 일치", unsupported: "근거 불일치" } as const;

function SentenceView({
  s,
  evidence,
  onMetric,
  onEvidence,
}: {
  s: MemoSentence;
  evidence: Map<string, MemoEvidence>;
  onMetric: (metricId: string) => void;
  onEvidence: (refId: string) => void;
}) {
  const fail = s.status === "fail";
  const tip = [
    `종류: ${KIND_LABEL[s.kind]}`,
    s.verdict ? `검증: ${VERDICT_LABEL[s.verdict]}${s.judge_reason ? ` — ${s.judge_reason}` : ""}` : null,
    s.revised ? `검증 실패 후 ${s.revised}회차에 재작성됨` : null,
    ...s.problems,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <li className={`ms ms-${s.kind}${fail ? " ms-fail" : ""}`} title={tip}>
      <span className={`ms-kind k-${s.kind}`}>{KIND_LABEL[s.kind]}</span>
      <span className="ms-text">
        {s.segments.map((seg, i) =>
          seg.type === "text" ? (
            <span key={i}>{seg.text}</span>
          ) : (
            <button
              key={i}
              className="ms-metric num"
              title={`${seg.label}\n계산: ${seg.formula}\n클릭: 원 데이터·공시 출처`}
              onClick={() => seg.metric_ids[0] && onMetric(seg.metric_ids[0])}
              disabled={!seg.metric_ids[0]}
            >
              {seg.text}
            </button>
          ),
        )}
        {s.evidence_ids.map((id) => {
          const e = evidence.get(id);
          return (
            <button key={id} className="ms-cite" onClick={() => onEvidence(id)} title={e ? `${e.ticker} ${e.form} ${e.section_label}` : id}>
              {e ? `${e.ticker} ${e.form} ${e.item ? `Item ${e.item}` : e.section_label}` : id}
            </button>
          );
        })}
        {s.revised ? <span className="ms-revised" title="검증 실패 후 재작성된 문장">재작성</span> : null}
        {fail && <span className="ms-warn">⚠ {s.problems[0] ?? "검증 실패"}</span>}
      </span>
    </li>
  );
}

/** 투자 검토 메모. 수치는 클릭하면 계산 근거, 공시 인용은 클릭하면 원문 문단. */
export function MemoView({
  memo,
  onMetric,
  onEvidence,
}: {
  memo: AgentMemo;
  onMetric: (metricId: string) => void;
  onEvidence: (refId: string) => void;
}) {
  const ev = new Map(memo.evidence.map((e) => [e.ref_id, e]));
  return (
    <article className="memo">
      <h2 className="memo-title">{memo.title}</h2>
      {memo.blocks.map((b) => (
        <section key={b.heading} className={b.heading === "핵심 요약" ? "memo-summary" : "memo-block"}>
          <h3>{b.heading}</h3>
          <ul>
            {b.sentences.map((s) => (
              <SentenceView key={s.id} s={s} evidence={ev} onMetric={onMetric} onEvidence={onEvidence} />
            ))}
          </ul>
        </section>
      ))}
    </article>
  );
}

export function EvidencePanel({ e, onClose }: { e: MemoEvidence; onClose: () => void }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="공시 근거">
        <button className="drawer-close" onClick={onClose} aria-label="닫기">×</button>
        <h2>{e.ref_id} · 공시 근거 원문</h2>
        <div style={{ fontSize: 16, fontWeight: 650, margin: "4px 0 2px" }}>
          {e.company} ({e.ticker})
        </div>
        <div className="card-note">
          {e.form} · 공시일 {e.filed} · 회계연도 종료 {e.report_date} · {e.section_label}
          {e.item && ` (Item ${e.item})`}
        </div>
        <section>
          <h3>검색 질문</h3>
          <div className="ok-item">{e.question}</div>
        </section>
        <section>
          <h3>{e.subheading || "원문"}</h3>
          <p className="ev-text" style={{ color: "var(--text-primary)" }}>{e.text}</p>
        </section>
        <section>
          <h3>출처</h3>
          <div className="fact">
            <dl>
              <dt>원문 위치</dt>
              <dd><a href={e.anchor_url} target="_blank" rel="noreferrer">SEC 원문에서 이 문단 보기 ↗</a></dd>
              <dt>접수번호</dt>
              <dd>{e.accn}</dd>
              <dt>관련도</dt>
              <dd className="num">{e.score.toFixed(3)} (리랭커)</dd>
              <dt>수집</dt>
              <dd>{e.retrieved_at.replace("T", " ").replace("+00:00", " UTC")}</dd>
            </dl>
          </div>
        </section>
      </aside>
    </>
  );
}
