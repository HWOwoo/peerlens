"""웹 API·Agent Tool이 공유하는 서비스 계층. 모든 수치는 metrics 모듈 계산값만 내보낸다."""

from __future__ import annotations

import statistics
from datetime import date
import threading
from functools import lru_cache
from typing import Any

from peerlens.edgar.client import EdgarClient
from peerlens.metrics.calc import METRICS, Align, MetricValue, compute_metrics, latest_reported_year, metrics_frame, table_year_col
from peerlens.metrics.facts import Fact
from peerlens.metrics.pipeline import load_facts
from peerlens.metrics.recent import compute_recent_metrics, recent_scale

_client: EdgarClient | None = None
_client_lock = threading.Lock()


def client() -> EdgarClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = EdgarClient()
        return _client


@lru_cache(maxsize=256)
def company_facts(ticker: str) -> tuple[Fact, ...]:
    return tuple(load_facts(client(), ticker))


@lru_cache(maxsize=256)
def company_metrics(ticker: str) -> tuple[MetricValue, ...]:
    """연간(FY) + 최근 12개월(TTM) + 최근 분기(Q) 지표."""
    facts = list(company_facts(ticker))
    return tuple(compute_metrics(facts)) + tuple(compute_recent_metrics(facts))


STALE_DAYS = 200  # 다른 회사 최신 기준일보다 이만큼 오래되면 '오래된 데이터'로 보고 중앙값에서 뺀다


def search_companies(query: str, limit: int = 10) -> list[dict[str, Any]]:
    q = query.strip().upper()
    if not q:
        return []
    rows = client().company_tickers().data.values()
    exact, prefix, contains = [], [], []
    for r in rows:
        t, name = r["ticker"].upper(), r["title"]
        item = {"ticker": t, "name": name, "cik": int(r["cik_str"])}
        if t == q:
            exact.append(item)
        elif t.startswith(q):
            prefix.append(item)
        elif q in name.upper():
            contains.append(item)
    return (exact + sorted(prefix, key=lambda x: len(x["ticker"])) + contains)[:limit]


def _company_info(ticker: str) -> dict[str, Any]:
    facts = company_facts(ticker)
    latest = max(facts, key=lambda f: f.period_end)
    return {
        "ticker": ticker,
        "name": latest.company,
        "cik": latest.cik,
        "currency": latest.unit,
        "latest_period_end": latest.period_end,
    }


def compare(target: str, peers: list[str], *, years: int = 3, align: Align = "calendar") -> dict[str, Any]:
    target = target.upper()
    tickers = [target] + [p.upper() for p in peers if p.upper() != target]
    tickers = list(dict.fromkeys(tickers))
    all_metrics = [m for t in tickers for m in company_metrics(t)]
    metrics = [m for m in all_metrics if m.period_type == "FY"]

    year_col = table_year_col(align)
    df = metrics_frame(metrics)
    latest = latest_reported_year(df, year_col)
    year_list = list(range(latest - years + 1, latest + 1))

    # 같은 연도에 두 기간이 매핑되면 최신 종료일 사용 (comparison_table과 동일 규칙)
    cell: dict[tuple[str, str, int], MetricValue] = {}
    for m in sorted(metrics, key=lambda m: m.period_end):
        y = getattr(m, year_col)
        if y in year_list:
            cell[(m.ticker, m.metric, y)] = m

    cells = [
        {
            "ticker": t, "metric": name, "year": y,
            "value": m.value, "metric_id": m.metric_id, "period_end": m.period_end,
            "fiscal_year": m.fiscal_year, "flags": m.flags,
        }
        for (t, name, y), m in cell.items()
    ]

    peer_stats = []
    for name in METRICS:
        for y in year_list:
            vals = [cell[(t, name, y)].value for t in tickers[1:] if (t, name, y) in cell and cell[(t, name, y)].value is not None]
            peer_stats.append({
                "metric": name, "year": y, "n": len(vals),
                "median": statistics.median(vals) if vals else None,
                "formula": f"median({name} of peers, n={len(vals)})",
            })

    missing = [t for t in tickers if (t, "gross_margin", latest) not in cell]
    recent = _recent_block(tickers, all_metrics)
    return {
        "target": target,
        "tickers": tickers,
        "companies": [_company_info(t) for t in tickers],
        "align": align,
        "year_label": "CY" if align == "calendar" else "FY",
        "years": year_list,
        "latest_year": latest,
        "metrics": [{"name": d.name, "label": d.label_ko, "formula": d.formula} for d in METRICS.values()],
        "cells": cells,
        "peer_median": peer_stats,
        "missing_latest": missing,
        **recent,
    }


def _recent_block(tickers: list[str], metrics: list[MetricValue]) -> dict[str, Any]:
    """최근 12개월(TTM)·최근 분기(Q) 셀과 Peer 중앙값. 기준일이 회사마다 달라 회사별 기간 라벨을 함께 준다."""
    recent = [m for m in metrics if m.period_type in ("TTM", "Q")]
    ends = {(m.ticker, m.period_type): (m.period_end, m.period_label) for m in recent}
    newest = max((e for (_, k), (e, _) in ends.items() if k == "TTM"), default=None)
    stale = sorted({t for (t, k), (e, _) in ends.items()
                    if k == "TTM" and newest and (date.fromisoformat(newest) - date.fromisoformat(e)).days > STALE_DAYS})
    cells = [{
        "ticker": m.ticker, "metric": m.metric, "period_type": m.period_type, "value": m.value, "metric_id": m.metric_id,
        "period_end": m.period_end, "period_label": m.period_label, "flags": m.flags, "stale": m.ticker in stale,
    } for m in recent]
    medians = []
    for pt in ("TTM", "Q"):
        for name in METRICS:
            vals = [c["value"] for c in cells if c["period_type"] == pt and c["metric"] == name and c["ticker"] != tickers[0]
                    and c["value"] is not None and not c["stale"]]
            medians.append({"metric": name, "period_type": pt, "n": len(vals), "median": statistics.median(vals) if vals else None,
                            "formula": f"median({name} {pt} of peers, n={len(vals)}, 오래된 데이터 제외)"})
    periods = {t: {"ttm": ends.get((t, "TTM"), (None, None))[1], "ttm_end": ends.get((t, "TTM"), (None, None))[0],
                   "q": ends.get((t, "Q"), (None, None))[1], "q_end": ends.get((t, "Q"), (None, None))[0],
                   "scale": recent_scale(list(company_facts(t)))} for t in tickers}
    return {"recent_cells": cells, "recent_median": medians, "recent_periods": periods, "stale": stale}


_index = None
_index_lock = threading.Lock()


def filing_index():
    """Qdrant 로컬 모드는 한 프로세스에서 한 번만 열 수 있어 싱글턴으로 둔다."""
    global _index
    with _index_lock:
        if _index is None:
            import atexit

            from peerlens.retrieval.index import FilingIndex
            from peerlens.retrieval.providers import default_embedder, default_reranker

            _index = FilingIndex(default_embedder(), default_reranker())
            atexit.register(_index.qdrant.close)  # 종료 시 잠금 파일 정리
        return _index


def search_evidence(
    query: str,
    *,
    tickers: list[str] | None = None,
    sections: list[str] | None = None,
    k: int = 5,
    keywords: str | None = None,
) -> dict[str, Any]:
    import time

    idx = filing_index()
    t0 = time.perf_counter()
    with _index_lock:  # 로컬 Qdrant는 동시 접근에 안전하지 않다
        hits = idx.search(query, tickers=tickers, sections=sections, k=k, keyword_query=keywords)
    elapsed = time.perf_counter() - t0
    return {
        "query": query,
        "keywords": keywords,
        "elapsed_ms": round(elapsed * 1000),
        "pipeline": {
            "embed_model": idx.embedder.model,
            "rerank_model": idx.reranker.model if idx.reranker else None,
            "fusion": "RRF(k=60) of dense + BM25",
        },
        "results": [_hit_dict(e) for e in hits],
    }


def _hit_dict(e) -> dict[str, Any]:
    return {
        "rank": e.rank, "score": e.score, "fused_rank": e.fused_rank, "dense_rank": e.dense_rank, "sparse_rank": e.sparse_rank,
        **{k_: e.chunk[k_] for k_ in (
            "chunk_id", "ticker", "company", "form", "accn", "filed", "report_date", "section", "section_label",
            "item", "subheading", "text", "source_url", "anchor_url", "retrieved_at",
        )},
    }


def search_evidence_per_ticker(
    query: str, k_by_ticker: dict[str, int], *, sections: list[str] | None = None, keywords: str | None = None
) -> dict[str, Any]:
    import time

    idx = filing_index()
    t0 = time.perf_counter()
    with _index_lock:
        by_t = idx.search_per_ticker(query, k_by_ticker, sections=sections, keyword_query=keywords)
    return {
        "query": query,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "results": [_hit_dict(e) for t in k_by_ticker for e in by_t.get(t, [])],
    }


def indexed_filings() -> list[dict[str, Any]]:
    idx = filing_index()
    with _index_lock:
        return idx.indexed_filings()


def metric_detail(metric_id: str) -> dict[str, Any]:
    ticker = metric_id.split(":", 1)[0]
    m = next((m for m in company_metrics(ticker) if m.metric_id == metric_id), None)
    if m is None:
        raise KeyError(metric_id)
    facts = {f.fact_id: f for f in company_facts(ticker)}
    return {**m.to_dict(), "input_facts": [facts[i].to_dict() for i in m.inputs]}
