import type { TraceEvent } from "../api";

const ICON: Record<string, string> = { tool: "→", tool_result: "←", llm: "✦", result: "•", error: "✖" };
const LABEL: Record<string, string> = { tool: "도구 호출", tool_result: "도구 결과", llm: "LLM", result: "판단·결과", error: "오류" };

type Step = { node: string; title: string; start: number; end?: number; items: TraceEvent[]; error: boolean };

function group(events: TraceEvent[]): Step[] {
  const steps: Step[] = [];
  for (const e of events) {
    if (e.type === "node_start") steps.push({ node: e.node, title: e.title, start: e.t_ms, items: [], error: false });
    else if (e.type === "node_end") {
      const s = [...steps].reverse().find((x) => x.node === e.node && x.end == null);
      if (s) s.end = e.t_ms;
    } else if (e.type in ICON) {
      const s = steps[steps.length - 1];
      if (s) {
        s.items.push(e);
        if (e.type === "error") s.error = true;
      } else if (e.type === "error") steps.push({ node: "run", title: "실행", start: e.t_ms, items: [e], error: true });
    }
  }
  return steps;
}

/** Agent 실행 과정 (Trace). 단계별로 도구 호출·LLM 호출·판단 결과를 시간순으로 보여준다. */
export function TraceTimeline({ events, running }: { events: TraceEvent[]; running: boolean }) {
  const steps = group(events);
  const last = events[events.length - 1];
  return (
    <ol className="trace">
      {steps.map((s, i) => {
        const active = running && s.end == null && i === steps.length - 1;
        return (
          <li key={`${s.node}-${i}`} className={`trace-step${active ? " active" : ""}${s.error ? " err" : ""}`}>
            <div className="trace-head">
              <span className="trace-dot" aria-hidden>{s.error ? "!" : s.end != null ? "✓" : active ? "" : "·"}</span>
              <b>{s.title}</b>
              <span className="trace-time num">
                {s.end != null ? `${((s.end - s.start) / 1000).toFixed(1)}초` : active ? "진행 중…" : ""}
              </span>
            </div>
            {s.items.length > 0 && (
              <ul className="trace-items">
                {s.items.map((e) => (
                  <li key={e.seq} className={`ti ti-${e.type}`} title={LABEL[e.type]}>
                    <span className="ti-icon" aria-hidden>{ICON[e.type]}</span>
                    <span className="ti-title">{e.title}</span>
                    {e.detail && <span className="ti-detail">{e.detail}</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
      {running && last?.type !== "node_start" && steps.length === 0 && <li className="trace-step active"><div className="trace-head"><b>시작 중…</b></div></li>}
    </ol>
  );
}
