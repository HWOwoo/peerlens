import { useEffect, useMemo, useState } from "react";
import type { Comparison } from "../api";
import { describeFlag, flagLabel, pct, ppDiff } from "../format";
import { RankBar, TrendLines } from "./Charts";

/** 비교 기간: 연도(연간) | 최근 12개월 | 최근 분기 */
type Period = number | "TTM" | "Q";

type CellLike = { value: number | null; metric_id: string; flags: string[]; stale?: boolean; period_label?: string };

const PERIOD_NAME: Record<"TTM" | "Q", string> = { TTM: "최근 12개월", Q: "최근 분기" };

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
  const hasRecent = (data.recent_cells?.length ?? 0) > 0;
  const [chartMetric, setChartMetric] = useState(initialMetric);
  const [period, setPeriod] = useState<Period>(hasRecent ? "TTM" : data.latest_year);
  useEffect(() => setPeriod(hasRecent ? "TTM" : data.latest_year), [data, hasRecent]);

  const idx = useMemo(() => {
    const m = new Map<string, CellLike>();
    data.cells.forEach((c) => m.set(`${c.ticker}|${c.metric}|${c.year}`, c));
    data.recent_cells?.forEach((c) => m.set(`${c.ticker}|${c.metric}|${c.period_type}`, c));
    return m;
  }, [data]);
  const cell = (t: string, metric: string, p: Period) => idx.get(`${t}|${metric}|${p}`);
  const median = (metric: string, p: Period) =>
    typeof p === "number"
      ? data.peer_median.find((x) => x.metric === metric && x.year === p)?.median ?? null
      : data.recent_median?.find((x) => x.metric === metric && x.period_type === p)?.median ?? null;
  const nameOf = (t: string) => data.companies.find((c) => c.ticker === t)?.name;
  const periodText = (p: Period) => (typeof p === "number" ? `${data.year_label}${p}` : PERIOD_NAME[p]);
  const tickerPeriod = (t: string, p: Period) => {
    if (typeof p === "number") return null;
    const rp = data.recent_periods?.[t];
    return p === "TTM" ? rp?.ttm : rp?.q;
  };
  const metricsFor = (p: Period) => (p === "Q" ? data.metrics.filter((m) => ["revenue_growth", "gross_margin", "operating_margin", "net_margin"].includes(m.name)) : data.metrics);
  const kpiPeriod: Period = hasRecent ? "TTM" : data.latest_year;
  const stale = data.stale ?? [];
  const options: Period[] = [...(hasRecent ? (["TTM", "Q"] as Period[]) : []), ...[...data.years].reverse()];
  const metricLabel = (name: string, label: string, p: Period) => (p === "Q" && name === "revenue_growth" ? "매출 성장률(전년 동기 대비)" : label);

  return (
    <>
      {data.missing_latest.length > 0 && (
        <div className="notice">
          ⓘ {data.missing_latest.join(", ")}: {data.year_label}{data.latest_year} 연간 공시 데이터가 아직 SEC XBRL에 없어 빈칸으로 표시합니다. Peer 중앙값 계산에서도 제외됩니다.
        </div>
      )}
      {hasRecent && stale.length > 0 && (
        <div className="notice">
          ⓘ {stale.join(", ")}: 최근 분기 데이터가 SEC XBRL에 없어 최근 12개월 대신 {stale.map((t) => data.recent_periods?.[t]?.ttm).join(", ")} 기준입니다. 최근 기간 Peer 중앙값에서는 제외합니다.
        </div>
      )}

      <section className="card" aria-label="요약">
        <div className="card-head">
          <h2 className="card-title">
            {data.target} <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>{nameOf(data.target)}</span>
          </h2>
          <span className="card-note">
            vs Peer {data.tickers.length - 1}개사 중앙값 · {kpiPeriod === "TTM" ? `최근 12개월 (${data.recent_periods?.[data.target]?.ttm ?? ""})` : periodText(kpiPeriod)} · 타일을 누르면 계산 근거
          </span>
          {hasRecent && data.recent_periods?.[data.target]?.scale && (
            <span className="card-note" style={{ marginLeft: "auto" }}>
              매출 규모 <b className="num">{fmtScale(data.recent_periods[data.target].scale!)}</b>
            </span>
          )}
        </div>
        <div className="kpis">
          {data.metrics.map((m) => {
            const c = cell(data.target, m.name, kpiPeriod);
            const med = median(m.name, kpiPeriod);
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
            <p className="chart-title">{periodText(period === "Q" && !["revenue_growth", "gross_margin", "operating_margin", "net_margin"].includes(chartMetric) ? "TTM" : period)} 회사별</p>
            <RankBar
              target={data.target}
              median={median(chartMetric, period === "Q" && !metricsFor("Q").some((m) => m.name === chartMetric) ? "TTM" : period)}
              points={data.tickers.map((t) => {
                const p: Period = period === "Q" && !metricsFor("Q").some((m) => m.name === chartMetric) ? "TTM" : period;
                const c = cell(t, chartMetric, p);
                return { ticker: t, value: c?.value ?? null, metricId: c?.metric_id };
              })}
              onSelect={onOpenMetric}
            />
          </div>
          <div>
            <p className="chart-title">연도별 추이 (연간 공시)</p>
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
          <div className="seg" role="group" aria-label="기간" style={{ marginLeft: "auto" }}>
            {options.map((p) => (
              <button key={String(p)} aria-pressed={period === p} onClick={() => setPeriod(p)}>{periodText(p)}</button>
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
                    <span className="co">{tickerPeriod(t, period) ?? nameOf(t)}</span>
                    {typeof period !== "number" && stale.includes(t) && <span className="co stale">⚠ 오래된 데이터</span>}
                  </th>
                ))}
                <th className="col-median">Peer 중앙값</th>
              </tr>
            </thead>
            <tbody>
              {metricsFor(period).map((m) => (
                <tr key={m.name}>
                  <td className="metric-name">
                    {metricLabel(m.name, m.label, period)}
                    <small>{period === "TTM" ? "최근 12개월 = 연간 + 올해 누적 − 작년 같은 기간 누적" : m.formula}</small>
                  </td>
                  {data.tickers.map((t) => {
                    const c = cell(t, m.name, period);
                    return (
                      <td key={t} className={t === data.target ? "col-target" : undefined}>
                        {c && c.value != null ? (
                          <button className={`cell-btn${c.stale ? " is-stale" : ""}`} aria-current={activeMetric === c.metric_id} onClick={() => onOpenMetric(c.metric_id)}>
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
                  <td className="col-median num">{pct(median(m.name, period))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function fmtScale(s: { value: number; unit: string; label: string }) {
  const sym: Record<string, string> = { USD: "$", EUR: "€", TWD: "NT$" };
  return `${sym[s.unit] ?? ""}${(s.value / 1e9).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}B${sym[s.unit] ? "" : ` ${s.unit}`}`;
}
