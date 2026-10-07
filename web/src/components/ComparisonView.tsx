import { useEffect, useMemo, useState } from "react";
import type { Cell, Comparison } from "../api";
import { describeFlag, flagLabel, pct, ppDiff } from "../format";
import { RankBar, TrendLines } from "./Charts";

/** 비교 결과 화면 (요약 타일·차트·비교표). 대시보드와 Agent 결과가 함께 쓴다. */
export function ComparisonView({
  data,
  onOpenMetric,
  activeMetric,
  initialMetric = "operating_margin",
}: {
  data: Comparison;
  onOpenMetric: (metricId: string) => void;
  activeMetric?: string | null;
  initialMetric?: string;
}) {
  const [chartMetric, setChartMetric] = useState(initialMetric);
  const [tableYear, setTableYear] = useState<number>(data.latest_year);
  useEffect(() => setTableYear(data.latest_year), [data]);

  const idx = useMemo(() => {
    const m = new Map<string, Cell>();
    data.cells.forEach((c) => m.set(`${c.ticker}|${c.metric}|${c.year}`, c));
    return m;
  }, [data]);
  const cell = (t: string, metric: string, y: number) => idx.get(`${t}|${metric}|${y}`);
  const median = (metric: string, y: number) => data.peer_median.find((p) => p.metric === metric && p.year === y)?.median ?? null;
  const nameOf = (t: string) => data.companies.find((c) => c.ticker === t)?.name;

  return (
    <>
      {data.missing_latest.length > 0 && (
        <div className="notice">
          ⓘ {data.missing_latest.join(", ")}: {data.year_label}{data.latest_year} 연간 공시 데이터가 아직 SEC XBRL에 없어 빈칸으로 표시합니다. Peer 중앙값 계산에서도 제외됩니다.
        </div>
      )}

      <section className="card" aria-label="요약">
        <div className="card-head">
          <h2 className="card-title">
            {data.target} <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>{nameOf(data.target)}</span>
          </h2>
          <span className="card-note">
            vs Peer {data.tickers.length - 1}개사 중앙값 · {data.year_label}{data.latest_year} · 타일을 누르면 계산 근거
          </span>
        </div>
        <div className="kpis">
          {data.metrics.map((m) => {
            const c = cell(data.target, m.name, data.latest_year);
            const med = median(m.name, data.latest_year);
            const diff = c?.value != null && med != null ? c.value - med : null;
            return (
              <button key={m.name} className="kpi" onClick={() => c && onOpenMetric(c.metric_id)} disabled={!c}>
                <div className="kpi-label">{m.label}</div>
                <div className="kpi-value num">{pct(c?.value)}</div>
                <div className="kpi-sub num">
                  중앙값 {pct(med)} · <b>{ppDiff(diff)}</b>
                  {c && c.flags.length > 0 && <span className="flag-dot" title={c.flags.map((f) => describeFlag(f).text).join("\n")}>⚑</span>}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="card" aria-label="비교 차트">
        <div className="card-head">
          <h2 className="card-title">지표 비교</h2>
          <div className="seg" role="group" aria-label="차트 지표" style={{ marginLeft: "auto" }}>
            {data.metrics.map((m) => (
              <button key={m.name} aria-pressed={chartMetric === m.name} onClick={() => setChartMetric(m.name)}>
                {m.label.replace("(YoY)", "")}
              </button>
            ))}
          </div>
        </div>
        <div className="card-body charts">
          <div>
            <p className="chart-title">{data.year_label}{data.latest_year} 회사별</p>
            <RankBar
              target={data.target}
              median={median(chartMetric, data.latest_year)}
              points={data.tickers.map((t) => {
                const c = cell(t, chartMetric, data.latest_year);
                return { ticker: t, value: c?.value ?? null, metricId: c?.metric_id };
              })}
              onSelect={onOpenMetric}
            />
          </div>
          <div>
            <p className="chart-title">연도별 추이</p>
            <TrendLines
              years={data.years}
              yearLabel={data.year_label}
              target={data.target}
              series={data.tickers.map((t) => ({ ticker: t, values: data.years.map((y) => cell(t, chartMetric, y)?.value ?? null) }))}
              median={data.years.map((y) => median(chartMetric, y))}
            />
          </div>
        </div>
      </section>

      <section className="card" aria-label="비교표">
        <div className="card-head">
          <h2 className="card-title">비교표</h2>
          <span className="card-note">값을 누르면 공시 원문·XBRL 태그·계산식을 확인할 수 있습니다 · ⚑ 데이터 품질 참고</span>
          <div className="seg" role="group" aria-label="연도" style={{ marginLeft: "auto" }}>
            {[...data.years].reverse().map((y) => (
              <button key={y} aria-pressed={tableYear === y} onClick={() => setTableYear(y)}>{data.year_label}{y}</button>
            ))}
          </div>
        </div>
        <div className="card-body table-wrap">
          <table className="cmp">
            <thead>
              <tr>
                <th>지표</th>
                {data.tickers.map((t) => (
                  <th key={t} className={t === data.target ? "col-target" : undefined}>
                    {t}
                    <span className="co">{nameOf(t)}</span>
                  </th>
                ))}
                <th className="col-median">Peer 중앙값</th>
              </tr>
            </thead>
            <tbody>
              {data.metrics.map((m) => (
                <tr key={m.name}>
                  <td className="metric-name">
                    {m.label}
                    <small>{m.formula}</small>
                  </td>
                  {data.tickers.map((t) => {
                    const c = cell(t, m.name, tableYear);
                    return (
                      <td key={t} className={t === data.target ? "col-target" : undefined}>
                        {c && c.value != null ? (
                          <button className="cell-btn" aria-current={activeMetric === c.metric_id} onClick={() => onOpenMetric(c.metric_id)}>
                            {pct(c.value)}
                            {c.flags.length > 0 && (
                              <span className="flag-dot" title={c.flags.map((f) => `[${flagLabel[describeFlag(f).kind]}] ${describeFlag(f).text}`).join("\n")}>
                                ⚑
                              </span>
                            )}
                          </button>
                        ) : (
                          <span className="empty" title={c ? c.flags.map((f) => describeFlag(f).text).join("\n") : "미공시"}>–</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="col-median num">{pct(median(m.name, tableYear))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
