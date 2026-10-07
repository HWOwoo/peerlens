import { useEffect, useState } from "react";
import { api, type Fact, type MetricDetail } from "../api";
import { conceptKo, describeFlag, flagLabel, money, pct } from "../format";

/** 계산식의 변수(revenue[t], equity[t-1] …)를 실제 공시 값으로 치환해 보여준다. */
function substitute(formula: string, d: MetricDetail): string {
  return formula.replace(/([a-z_]+)(\[t(?:-1)?\])?/g, (whole, concept: string, idx?: string) => {
    const facts = d.input_facts.filter((f) => f.concept === concept);
    if (!facts.length) return whole;
    const cur = facts.find((f) => f.period_end === d.period_end);
    const prev = facts.find((f) => f.period_end !== d.period_end);
    const f = idx === "[t-1]" ? prev : cur ?? facts[0];
    return f ? money(f.value, f.unit) : whole;
  });
}

function periodName(f: Fact) {
  const days = (Date.parse(f.period_end) - Date.parse(f.period_start!)) / 86400000;
  if (days > 340) return "연간";
  if (days < 100) return "3개월";
  return `${Math.round(days / 30.4)}개월 누적`;
}

function FactCard({ f, current }: { f: Fact; current: string }) {
  const period = f.period_start ? `${f.period_start} ~ ${f.period_end}` : `${f.period_end} 시점`;
  return (
    <div className="fact">
      <div className="fact-top">
        <span>
          {conceptKo(f.concept)}
          {f.period_start && <span className="card-note"> · {periodName(f)}</span>}
          {!f.period_start && f.period_end !== current && <span className="card-note"> (전년)</span>}
        </span>
        <span className="num">{money(f.value, f.unit)}</span>
      </div>
      <dl>
        <dt>회계기간</dt>
        <dd>FY{f.fiscal_year}{f.annual === false ? " 분기 공시" : ""} · {period}</dd>
        <dt>XBRL 태그</dt>
        <dd>{f.taxonomy}:{f.tag}{!f.exact_tag && " (대체 태그)"}</dd>
        <dt>공시</dt>
        <dd>
          <a href={f.filing_url} target="_blank" rel="noreferrer">{f.form} · {f.accn}</a> · 공시일 {f.filed}
        </dd>
        {f.restated && f.original_value != null && (
          <>
            <dt>최초 공시값</dt>
            <dd className="num">{money(f.original_value, f.unit)} (이후 공시에서 수정)</dd>
          </>
        )}
        <dt>수집</dt>
        <dd>
          <a href={f.api_url} target="_blank" rel="noreferrer">SEC XBRL API</a> · {f.retrieved_at.replace("T", " ").replace("+00:00", " UTC")}
        </dd>
      </dl>
    </div>
  );
}

export function ProvenancePanel({ metricId, companyName, onClose }: { metricId: string; companyName?: string; onClose: () => void }) {
  const [d, setD] = useState<MetricDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    api.metric(metricId).then(setD).catch((e: Error) => setErr(e.message));
  }, [metricId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="수치 출처">
        <button className="drawer-close" onClick={onClose} aria-label="닫기">×</button>
        {err && <div className="error">{err}</div>}
        {!d && !err && <div className="skeleton">출처 불러오는 중…</div>}
        {d && (
          <>
            <h2>
              {d.ticker}{companyName ? ` · ${companyName}` : ""} · {d.period_type && d.period_type !== "FY" ? d.period_label : `FY${d.fiscal_year}`} (기간 종료 {d.period_end})
            </h2>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span style={{ fontSize: 16, fontWeight: 650 }}>{d.label_ko}</span>
              <span className="big num">{pct(d.value)}</span>
            </div>

            <section>
              <h3>계산식 — 수치는 모두 코드로 계산 (LLM 생성 아님)</h3>
              <div className="formula">
                {d.formula}
                <br />= {d.explain ?? substitute(d.formula, d)}
                <br />= {pct(d.value, 2)}
                {d.period_type === "TTM" && (
                  <>
                    <br />
                    <span className="card-note">최근 12개월(TTM) = 최근 연간 + 올해 누적 − 작년 같은 기간 누적</span>
                  </>
                )}
              </div>
            </section>

            <section>
              <h3>데이터 품질 점검</h3>
              {d.flags.length === 0 ? (
                <div className="ok-item">✓ 표준 태그 원값만 사용, 재작성·대체 없음</div>
              ) : (
                d.flags.map((fl) => {
                  const info = describeFlag(fl);
                  return (
                    <div className="flag-item" key={fl}>
                      <b>{flagLabel[info.kind]}</b>
                      <span>{info.text}</span>
                    </div>
                  );
                })
              )}
            </section>

            <section>
              <h3>원 데이터 ({d.input_facts.length}건) — 공시 원문 링크</h3>
              {d.input_facts.map((f) => (
                <FactCard key={f.fact_id} f={f} current={d.period_end} />
              ))}
            </section>
          </>
        )}
      </aside>
    </>
  );
}
