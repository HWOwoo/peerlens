"""티커 → 최신 연간 공시 → 섹션 → 청크 → 색인."""

from __future__ import annotations

from dataclasses import dataclass

from peerlens.edgar.client import EdgarClient
from peerlens.edgar.filings import (
    FilingRef,
    fetch_filing_html,
    html_to_blocks,
    latest_annual_filing,
    latest_quarterly_filing,
    split_sections,
)
from peerlens.retrieval.chunking import Chunk, chunk_section
from peerlens.retrieval.index import FilingIndex


@dataclass
class IndexReport:
    ticker: str
    filing: FilingRef | None
    sections: dict[str, int]  # 섹션별 청크 수
    added: int = 0
    skipped: int = 0
    error: str | None = None


def _filing_chunks(client: EdgarClient, ref: FilingRef) -> list[Chunk]:
    html, retrieved_at = fetch_filing_html(client, ref)
    sections = split_sections(html_to_blocks(html), ref.form)
    return [c for sec in sections.values() for c in chunk_section(ref, sec, retrieved_at)]


def build_chunks(client: EdgarClient, ticker: str, *, quarterly: bool = True) -> tuple[FilingRef, list[Chunk]]:
    """최신 연간 공시 + (있으면) 그 이후의 최신 10-Q. 반환 ref는 연간 공시."""
    ref = latest_annual_filing(client, ticker)
    chunks = _filing_chunks(client, ref)
    if quarterly and (q := latest_quarterly_filing(client, ticker)) is not None:
        chunks += _filing_chunks(client, q)
    return ref, chunks


def index_tickers(index: FilingIndex, client: EdgarClient, tickers: list[str]) -> list[IndexReport]:
    reports = []
    for t in tickers:
        try:
            ref, chunks = build_chunks(client, t.upper())
        except Exception as e:  # 한 회사 실패가 전체 색인을 막지 않게
            reports.append(IndexReport(t.upper(), None, {}, error=str(e)))
            continue
        by_sec: dict[str, int] = {}
        for c in chunks:
            key = c.section if c.form != "10-Q" else f"10-Q {c.section}"
            by_sec[key] = by_sec.get(key, 0) + 1
        if not chunks:
            reports.append(IndexReport(t.upper(), ref, by_sec, error="섹션을 찾지 못함 (비표준 공시 형식)"))
            continue
        added, skipped = index.index_chunks(chunks)
        reports.append(IndexReport(t.upper(), ref, by_sec, added, skipped))
    return reports
