import { useState, type ReactNode } from "react";
import { pct } from "../format";

export type Point = { ticker: string; value: number | null; metricId?: string };

type Tip = { x: number; y: number; content: ReactNode } | null;

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  const left = Math.min(tip.x + 14, window.innerWidth - 220);
  return (
    <div className="tooltip" style={{ left, top: tip.y + 14 }}>
      {tip.content}
    </div>
  );
}

/** 값 범위에 0을 포함시키고 보기 좋은 눈금으로 확장. */
function niceDomain(values: number[]): [number, number, number[]] {
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (lo === hi) hi = lo + 0.1;
  const span = hi - lo;
  const raw = span / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return [lo, hi, ticks];
}

/** 끝만 둥근(4px) 막대. 기준선 쪽은 각지게 둔다. */
function barPath(x0: number, x1: number, y: number, h: number, r = 4) {
  const left = Math.min(x0, x1);
  const right = Math.max(x0, x1);
  const w = right - left;
  const rr = Math.min(r, w / 2, h / 2);
  if (w < 0.5) return "";
  if (x1 >= x0) {
    return `M${left},${y}H${right - rr}Q${right},${y} ${right},${y + rr}V${y + h - rr}Q${right},${y + h} ${right - rr},${y + h}H${left}Z`;
  }
  return `M${right},${y}H${left + rr}Q${left},${y} ${left},${y + rr}V${y + h - rr}Q${left},${y + h} ${left + rr},${y + h}H${right}Z`;
}

type BarProps = {
  points: Point[];
  target: string;
  median: number | null;
  onSelect?: (metricId: string) => void;
};

/** 최신 연도 회사별 수평 막대 — 대상 기업만 강조색, Peer는 중립 회색, Peer 중앙값 점선. */
export function RankBar({ points, target, median, onSelect }: BarProps) {
  const [tip, setTip] = useState<Tip>(null);
  const rows = [...points].sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity));
  const vals = rows.map((r) => r.value).filter((v): v is number => v != null);
  const [lo, hi, ticks] = niceDomain(median != null ? [...vals, median] : vals);

  const W = 520, rowH = 30, barH = 16, padL = 60, padR = 56, padT = 8, padB = 24;
  const H = padT + rows.length * rowH + padB;
  const x = (v: number) => padL + ((v - lo) / (hi - lo)) * (W - padL - padR);

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="회사별 비교 막대 차트">
        <g className="tick">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={padT} y2={H - padB} />
              <text className="label" x={x(t)} y={H - 6} textAnchor="middle">{pct(t, 0)}</text>
            </g>
          ))}
        </g>
        <line className="zero" x1={x(0)} x2={x(0)} y1={padT} y2={H - padB} strokeWidth={1} />
        {rows.map((r, i) => {
          const y = padT + i * rowH + (rowH - barH) / 2;
          const isT = r.ticker === target;
          const v = r.value;
          return (
            <g
              key={r.ticker}
              style={{ cursor: r.metricId ? "pointer" : "default" }}
              onMouseMove={(e) =>
                setTip({
                  x: e.clientX,
                  y: e.clientY,
                  content: (
                    <>
                      <div className="tt-head">{r.ticker}{isT ? " (대상)" : ""}</div>
                      <div className="tt-row">값 <b className="num">{pct(v)}</b></div>
                      {median != null && v != null && (
                        <div className="tt-row">Peer 중앙값 대비 <b className="num">{((v - median) * 100).toFixed(1)}%p</b></div>
                      )}
                      {r.metricId && <div className="tt-row" style={{ marginTop: 4 }}>클릭: 출처 보기</div>}
                    </>
                  ),
                })
              }
              onMouseLeave={() => setTip(null)}
              onClick={() => r.metricId && onSelect?.(r.metricId)}
            >
              <rect x={0} y={padT + i * rowH} width={W} height={rowH} fill="transparent" />
              <text
                x={padL - 10}
                y={y + barH / 2 + 4}
                textAnchor="end"
                style={{ fontSize: 12, fontWeight: isT ? 700 : 500, fill: isT ? "var(--accent-ink)" : "var(--text-secondary)" }}
              >
                {r.ticker}
              </text>
              {v != null ? (
                <>
                  <path d={barPath(x(0), x(v), y, barH)} fill={isT ? "var(--accent)" : "var(--peer)"} />
                  <text
                    className="num"
                    x={v >= 0 ? x(v) + 6 : x(v) - 6}
                    y={y + barH / 2 + 4}
                    textAnchor={v >= 0 ? "start" : "end"}
                    style={{ fontSize: 11.5, fontWeight: isT ? 700 : 400, fill: isT ? "var(--text-primary)" : "var(--text-muted)" }}
                  >
                    {pct(v)}
                  </text>
                </>
              ) : (
                <text x={x(0) + 6} y={y + barH / 2 + 4} className="label">미공시</text>
              )}
            </g>
          );
        })}
        {median != null && (
          <g>
            <line x1={x(median)} x2={x(median)} y1={padT - 4} y2={H - padB} stroke="var(--text-secondary)" strokeWidth={1.5} strokeDasharray="4 3" />
          </g>
        )}
      </svg>
      <div className="legend">
        <span><i style={{ background: "var(--accent)" }} />대상 기업</span>
        <span><i style={{ background: "var(--peer)" }} />Peer</span>
        <span><i style={{ background: "repeating-linear-gradient(90deg, var(--text-secondary) 0 4px, transparent 4px 7px)" }} />Peer 중앙값</span>
      </div>
      <Tooltip tip={tip} />
    </div>
  );
}

type Series = { ticker: string; values: (number | null)[] };

type TrendProps = {
  years: number[];
  yearLabel: string;
  series: Series[];
  target: string;
  median: (number | null)[];
};

/** 연도별 추이 — 대상 기업 굵은 강조선, Peer 얇은 회색선, 중앙값 점선. 세로 크로스헤어 툴팁. */
export function TrendLines({ years, yearLabel, series, target, median }: TrendProps) {
  const [hover, setHover] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip>(null);
  const all = [...series.flatMap((s) => s.values), ...median].filter((v): v is number => v != null);
  const [lo, hi, ticks] = niceDomain(all);

  const W = 520, H = 250, padL = 44, padR = 96, padT = 12, padB = 26;
  const x = (i: number) => padL + (years.length === 1 ? 0.5 : i / (years.length - 1)) * (W - padL - padR);
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  const line = (vals: (number | null)[]) => {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
      pen = true;
    });
    return d;
  };

  const ordered = [...series.filter((s) => s.ticker !== target), ...series.filter((s) => s.ticker === target)];
  const tSeries = series.find((s) => s.ticker === target);
  const lastIdx = (vals: (number | null)[]) => vals.reduce<number>((acc, v, i) => (v != null ? i : acc), -1);

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const local = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    const step = years.length > 1 ? (W - padL - padR) / (years.length - 1) : 1;
    const i = Math.max(0, Math.min(years.length - 1, Math.round((local.x - padL) / step)));
    setHover(i);
    const rows = series
      .map((s) => ({ t: s.ticker, v: s.values[i] }))
      .sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity));
    setTip({
      x: e.clientX,
      y: e.clientY,
      content: (
        <>
          <div className="tt-head">{yearLabel}{years[i]}</div>
          {rows.map((r) => (
            <div className="tt-row" key={r.t} style={r.t === target ? { color: "var(--accent-ink)" } : undefined}>
              {r.t}{r.t === target ? " (대상)" : ""} <b className="num">{pct(r.v)}</b>
            </div>
          ))}
          <div className="tt-row" style={{ borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 4 }}>
            Peer 중앙값 <b className="num">{pct(median[i])}</b>
          </div>
        </>
      ),
    });
  };

  const tLast = tSeries ? lastIdx(tSeries.values) : -1;
  const mLast = lastIdx(median);
  // 끝 라벨 겹침 방지: 대상과 중앙값 라벨이 가까우면 벌린다
  let tLabelY = tLast >= 0 ? y(tSeries!.values[tLast]!) : 0;
  let mLabelY = mLast >= 0 ? y(median[mLast]!) : 0;
  if (tLast >= 0 && mLast >= 0 && Math.abs(tLabelY - mLabelY) < 14) {
    const mid = (tLabelY + mLabelY) / 2;
    const up = tLabelY <= mLabelY;
    tLabelY = mid + (up ? -7 : 7);
    mLabelY = mid + (up ? 7 : -7);
  }

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="연도별 추이 선 차트">
        <g className="tick">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
              <text className="label" x={padL - 8} y={y(t) + 4} textAnchor="end">{pct(t, 0)}</text>
            </g>
          ))}
        </g>
        {lo < 0 && <line className="zero" x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} />}
        {years.map((yr, i) => (
          <text key={yr} className="label" x={x(i)} y={H - 6} textAnchor="middle">{yearLabel}{yr}</text>
        ))}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke="var(--border-strong)" />}
        {ordered.map((s) => {
          const isT = s.ticker === target;
          return (
            <g key={s.ticker}>
              <path
                d={line(s.values)}
                fill="none"
                stroke={isT ? "var(--accent)" : "var(--peer)"}
                strokeWidth={isT ? 2.5 : 1.5}
                strokeLinejoin="round"
                strokeLinecap="round"
                opacity={isT ? 1 : 0.85}
              />
              {s.values.map((v, i) =>
                v == null ? null : (
                  <circle
                    key={i}
                    cx={x(i)}
                    cy={y(v)}
                    r={isT ? 4.5 : hover === i ? 3.5 : 2.5}
                    fill={isT ? "var(--accent)" : "var(--peer)"}
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                ),
              )}
            </g>
          );
        })}
        <path d={line(median)} fill="none" stroke="var(--text-secondary)" strokeWidth={1.5} strokeDasharray="4 3" />
        {tLast >= 0 && (
          <text x={x(tLast) + 10} y={tLabelY + 4} style={{ fontSize: 12, fontWeight: 700, fill: "var(--accent-ink)" }}>
            {target} {pct(tSeries!.values[tLast])}
          </text>
        )}
        {mLast >= 0 && (
          <text x={x(mLast) + 10} y={mLabelY + 4} style={{ fontSize: 11.5, fill: "var(--text-secondary)" }}>
            중앙값 {pct(median[mLast])}
          </text>
        )}
        <rect
          x={padL - 10}
          y={padT}
          width={W - padL - padR + 20}
          height={H - padT - padB}
          fill="transparent"
          onMouseMove={onMove}
          onMouseLeave={() => {
            setHover(null);
            setTip(null);
          }}
        />
      </svg>
      <div className="legend">
        <span><i style={{ background: "var(--accent)" }} />{target} (대상)</span>
        <span><i style={{ background: "var(--peer)" }} />Peer {series.length - 1}개사</span>
        <span><i style={{ background: "repeating-linear-gradient(90deg, var(--text-secondary) 0 4px, transparent 4px 7px)" }} />Peer 중앙값</span>
      </div>
      <Tooltip tip={tip} />
    </div>
  );
}
