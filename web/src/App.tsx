import { useState } from "react";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { AgentPage } from "./pages/AgentPage";
import { ComparePage } from "./pages/ComparePage";

type Tab = "agent" | "compare";

export default function App() {
  const [tab, setTab] = useState<Tab>("agent");
  const [metric, setMetric] = useState<string | null>(null);

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
        <nav className="tabs" aria-label="화면">
          <button aria-pressed={tab === "agent"} onClick={() => setTab("agent")}>AI 투자메모</button>
          <button aria-pressed={tab === "compare"} onClick={() => setTab("compare")}>Peer 비교 대시보드</button>
        </nav>
        <span className="spacer" />
        <span className="source-badge">데이터: SEC EDGAR</span>
      </header>

      <main>
        {/* 탭을 바꿔도 상태(실행 결과)가 사라지지 않도록 숨기기만 한다 */}
        <div hidden={tab !== "agent"} className="page">
          <AgentPage onOpenMetric={setMetric} activeMetric={metric} />
        </div>
        <div hidden={tab !== "compare"} className="page">
          <ComparePage onOpenMetric={setMetric} activeMetric={metric} />
        </div>
        <footer>
          수치는 SEC EDGAR XBRL 원값과 코드 계산값만 사용합니다 · 공시 근거는 10-K·20-F 원문 인용 · 투자 판단은 사용자 책임
        </footer>
      </main>

      {metric && <ProvenancePanel metricId={metric} onClose={() => setMetric(null)} />}
    </>
  );
}
