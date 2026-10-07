import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Cell, type Comparison } from "./api";
import { CompanyPicker } from "./components/CompanyPicker";
import { EvidenceSearch } from "./components/EvidenceSearch";
import { RankBar, TrendLines } from "./components/Charts";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { describeFlag, flagLabel, pct, ppDiff } from "./format";

const SEMI_PEERS = ["AMD", "INTC", "AVGO", "QCOM", "TSM", "ASML"];

type Align = "calendar" | "fiscal";

function useComparison() {
  const [data, setData] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (target: string, peers: string[], years: number, align: Align) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.compare({ target, peers, years, align }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  return { data, loading, error, run };
}

export default function App() {
  const [target, setTarget] = useState("NVDA");
  const [peers, setPeers] = useState<string[]>(SEMI_PEERS);
  const [years, setYears] = useState(3);
  const [align, setAlign] = useState<Align>("calendar");
  const { data, loading, error, run } = useComparison();
  const [drawer, setDrawer] = useState<string | null>(null);
  const [chartMetric, setChartMetric] = useState("operating_margin");
  const [tableYear, setTableYear] = useState<number | null>(null);

  const submit = () => run(target, peers, years, align);
  useEffect(() => {
    submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => setTableYear(data?.latest_year ?? null), [data]);

  const idx = useMemo(() => {
    const m = new Map<string, Cell>();
    data?.cells.forEach((c) => m.set(`${c.ticker}|${c.metric}|${c.year}`, c));
    return m;
  }, [data]);
  const cell = (t: string, metric: string, y: number) => idx.get(`${t}|${metric}|${y}`);
  const median = (metric: string, y: number) => data?.peer_median.find((p) => p.metric === metric && p.year === y)?.median ?? null;
  const nameOf = (t: string) => data?.companies.find((c) => c.ticker === t)?.name;

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
            <circle cx="14" cy="14" r="9" fill="none" stroke="var(--accent)" strokeWidth="4" />
            <path d="M21 21l7 7" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
          </svg>
          PeerLens
        </div>
        <span className="brand-sub">공시 근거 기반 글로벌 Peer 비교 · 투자메모 AI Agent</span>
        <span className="spacer" />
        <span className="source-badge">데이터: SEC EDGAR XBRL</span>
      </header>

      <main>
        <section className="card query" aria-label="비교 조건">
          <div>
            <span className="field-label">대상 기업</span>
            <div className="chips" style={{ marginBottom: 8 }}>
              <span className="chip target">{target}</span>
            </div>
            <CompanyPicker ariaLabel="대상 기업 검색" placeholder="티커·회사명으로 변경" onPick={(c) => setTarget(c.ticker)} />
          </div>
          <div>
            <span className="field-label">
              Peer 기업 ({peers.length}) ·{" "}
              <button className="btn-link" onClick={() => setPeers(SEMI_PEERS.filter((p) => p !== target))}>반도체 기본 Peer</button>
            </span>
            <div className="chips">
              {peers.map((p) => (
                <span className="chip" key={p}>
                  {p}
                  <button aria-label={`${p} 제거`} onClick={() => setPeers(peers.filter((x) => x !== p))}>×</button>
                </span>
              ))}
              {peers.length < 12 && (
                <CompanyPicker
                  ariaLabel="Peer 추가"
                  placeholder="+ Peer 추가"
                  exclude={[target, ...peers]}
                  onPick={(c) => setPeers([...peers, c.ticker])}
                />
              )}
            </div>
          </div>
          <div className="query-actions">
            <div className="row">
              <div className="seg" role="group" aria-label="기간 정렬">
                <button aria-pressed={align === "calendar"} onClick={() => setAlign("calendar")} title="결산월이 달라도 같은 달력연도로 맞춤">달력연도</button>
                <button aria-pressed={align === "fiscal"} onClick={() => setAlign("fiscal")}>회계연도</button>
              </div>
              <div className="seg" role="group" aria-label="기간">
                {[3, 5].map((n) => (
                  <button key={n} aria-pressed={years === n} onClick={() => setYears(n)}>{n}년</button>
                ))}
              </div>
            </div>
            <button className="btn-primary" onClick={submit} disabled={loading || peers.length === 0}>
              {loading ? "공시 데이터 수집 중…" : "비교 실행"}
            </button>
          </div>
        </section>

        {error && <div className="error">⚠ {error}</div>}
        {!data && loading && <div className="card skeleton">SEC 공시에서 재무 데이터를 불러오는 중…</div>}

        {data && (
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
                    <button key={m.name} className="kpi" onClick={() => c && setDrawer(c.metric_id)} disabled={!c}>
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
                    onSelect={setDrawer}
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
                {tableYear != null && (
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
                                  <button className="cell-btn" aria-current={drawer === c.metric_id} onClick={() => setDrawer(c.metric_id)}>
                                    {pct(c.value)}
                                    {c.flags.length > 0 && (
                                      <span
                                        className="flag-dot"
                                        title={c.flags.map((f) => `[${flagLabel[describeFlag(f).kind]}] ${describeFlag(f).text}`).join("\n")}
                                      >
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
                )}
              </div>
            </section>
            <EvidenceSearch tickers={data.tickers} />
          </>
        )}

        <footer>
          수치는 SEC EDGAR XBRL 원값과 코드 계산값만 사용합니다 · 결산월이 다른 기업은 {align === "calendar" ? "달력연도(기간 중간 시점 기준)" : "각사 회계연도"}로 정렬 · 투자 판단은 사용자 책임
        </footer>
      </main>

      {drawer && <ProvenancePanel metricId={drawer} companyName={nameOf(drawer.split(":")[0])} onClose={() => setDrawer(null)} />}
    </>
  );
}
