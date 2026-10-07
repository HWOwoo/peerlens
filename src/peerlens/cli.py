"""PeerLens CLI.

    peerlens compare NVDA AMD INTC AVGO QCOM --years 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from peerlens.config import PROJECT_ROOT
from peerlens.edgar.client import EdgarClient
from peerlens.metrics.calc import comparison_table, latest_reported_year, metrics_frame, table_year_col
from peerlens.metrics.facts import facts_frame
from peerlens.metrics.pipeline import build_peer_dataset


def _fmt_pct(v: float) -> str:
    return "–" if pd.isna(v) else f"{v * 100:,.1f}%"


def cmd_compare(args: argparse.Namespace) -> int:
    with EdgarClient(offline=args.offline) as client:
        ds = build_peer_dataset(client, args.tickers)

    table = comparison_table(ds.metrics, years=args.years, align=args.align)
    print(table.map(_fmt_pct).to_markdown())

    year_col = table_year_col(args.align)
    latest = latest_reported_year(metrics_frame(ds.metrics), year_col)
    shown = [m for m in ds.metrics if latest - args.years < getattr(m, year_col) <= latest]
    missing = sorted({t for t in ds.tickers if not any(m.ticker == t and getattr(m, year_col) == latest for m in shown)})
    if missing:
        print(f"\n미공시(최신 {latest}년 데이터 없음): {', '.join(missing)}")
    flagged = [m for m in shown if m.flags]
    if flagged:
        print("\n품질 플래그:")
        for m in flagged:
            print(f"  {m.metric_id}: {', '.join(m.flags)}")

    out = Path(args.out) if args.out else PROJECT_ROOT / "data" / "output"
    out.mkdir(parents=True, exist_ok=True)
    stem = "_".join(ds.tickers)
    facts_frame(ds.facts).to_csv(out / f"{stem}_facts.csv", index=False, encoding="utf-8-sig")
    metrics_frame(ds.metrics).to_csv(out / f"{stem}_metrics.csv", index=False, encoding="utf-8-sig")
    table.to_csv(out / f"{stem}_comparison.csv", encoding="utf-8-sig")
    (out / f"{stem}_dataset.json").write_text(
        json.dumps({"facts": [f.to_dict() for f in ds.facts], "metrics": [m.to_dict() for m in ds.metrics]},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\n저장: {out / stem}_*.csv / _dataset.json")
    return 0


def _filing_index():
    from peerlens.retrieval.index import FilingIndex
    from peerlens.retrieval.providers import default_embedder, default_reranker

    return FilingIndex(default_embedder(), default_reranker())


def cmd_index(args: argparse.Namespace) -> int:
    from peerlens.retrieval.pipeline import index_tickers

    index = _filing_index()
    with EdgarClient() as client:
        for r in index_tickers(index, client, args.tickers):
            if r.error:
                print(f"{r.ticker:6s} 실패: {r.error}")
            else:
                assert r.filing
                print(f"{r.ticker:6s} {r.filing.form} {r.filing.filed} 신규 {r.added} / 기존 {r.skipped}  {r.sections}")
    print(f"색인 총 {index.count()} 청크")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    index = _filing_index()
    hits = index.search(args.query, tickers=args.tickers, sections=args.sections, k=args.k, keyword_query=args.keywords)
    for e in hits:
        c = e.chunk
        print(f"\n#{e.rank} 관련도 {e.score:.3f}  (RRF {e.fused_rank} / 의미 {e.dense_rank} / 키워드 {e.sparse_rank})")
        print(f"   {c['ticker']} {c['form']} 공시일 {c['filed']} · {c['section_label']} · {c['subheading'][:60]}")
        print(f"   {c['text'][:300]}…")
        print(f"   {c['anchor_url']}")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    from peerlens.agent import run_agent

    def show(e: dict) -> None:
        if e["type"] == "node_start":
            print(f"\n[{e['t_ms'] / 1000:6.1f}s] ▶ {e['title']}")
        elif e["type"] in ("tool", "tool_result", "llm", "result", "error"):
            mark = {"tool": "→", "tool_result": "←", "llm": "✦", "result": "•", "error": "✖"}[e["type"]]
            print(f"           {mark} {e['title']}  {e['detail'][:150]}")

    r = run_agent(args.request, show)
    if r["status"] != "ok":
        print(f"\n실패: {r['error']}")
        return 1
    memo = r["memo"]
    print(f"\n{'=' * 70}\n{memo['title']}\n")
    for b in memo["blocks"]:
        print(f"■ {b['heading']}")
        for s in b["sentences"]:
            text = "".join(seg["text"] for seg in s["segments"])
            ev = f" [{', '.join(s['evidence_ids'])}]" if s["evidence_ids"] else ""
            warn = " ⚠ " + "; ".join(s["problems"]) if s["status"] == "fail" else ""
            print(f"  - ({s['kind']}) {text}{ev}{warn}")
    print(f"\n통계: {memo['stats']}\nLLM: {r['llm_usage']} · {r['elapsed_ms'] / 1000:.1f}초 · 기록 data/runs/{r['run_id']}.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="peerlens")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="XBRL 기반 Peer 재무 비교표")
    c.add_argument("tickers", nargs="+")
    c.add_argument("--years", type=int, default=3)
    c.add_argument("--align", choices=["calendar", "fiscal"], default="calendar",
                   help="calendar: 결산월이 달라도 같은 달력연도로 정렬 (기본)")
    c.add_argument("--offline", action="store_true", help="캐시만 사용")
    c.add_argument("--out", help="출력 디렉터리 (기본 data/output)")
    c.set_defaults(func=cmd_compare)

    ix = sub.add_parser("index", help="최신 10-K/20-F 원문을 섹션별로 나눠 검색 색인")
    ix.add_argument("tickers", nargs="+")
    ix.set_defaults(func=cmd_index)

    s = sub.add_parser("search", help="공시 원문 근거 검색 (하이브리드 + 리랭커)")
    s.add_argument("query")
    s.add_argument("--tickers", nargs="*")
    s.add_argument("--sections", nargs="*", choices=["business", "risk_factors", "mdna", "market_risk"])
    s.add_argument("--keywords", help="키워드 검색용 영어 질의 (기본: query 그대로)")
    s.add_argument("-k", type=int, default=5)
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("agent", help="요청 한 문장 → Peer 선정·재무 비교·근거 검색·메모 작성·검증")
    a.add_argument("request")
    a.set_defaults(func=cmd_agent)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
