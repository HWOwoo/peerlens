import { useCallback, useEffect, useState } from "react";
import { api, type Comparison } from "../api";
import { CompanyPicker } from "../components/CompanyPicker";
import { ComparisonView } from "../components/ComparisonView";
import { EvidenceSearch } from "../components/EvidenceSearch";

const SEMI_PEERS = ["AMD", "INTC", "AVGO", "QCOM", "TSM", "ASML"];

type Align = "calendar" | "fiscal";

/** 수동 비교 대시보드: 대상·Peer를 직접 고르고 비교표·근거 검색을 본다. */
export function ComparePage({ onOpenMetric, activeMetric }: { onOpenMetric: (id: string) => void; activeMetric: string | null }) {
  const [target, setTarget] = useState("NVDA");
  const [peers, setPeers] = useState<string[]>(SEMI_PEERS);
  const [years, setYears] = useState(3);
  const [align, setAlign] = useState<Align>("calendar");
  const [data, setData] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.compare({ target, peers, years, align }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [target, peers, years, align]);

  useEffect(() => {
    submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
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
              <CompanyPicker ariaLabel="Peer 추가" placeholder="+ Peer 추가" exclude={[target, ...peers]} onPick={(c) => setPeers([...peers, c.ticker])} />
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
          <ComparisonView data={data} onOpenMetric={onOpenMetric} activeMetric={activeMetric} />
          <EvidenceSearch tickers={data.tickers} />
        </>
      )}
    </>
  );
}
