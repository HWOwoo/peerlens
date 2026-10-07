"""티커 → 최신 연간 공시 → 섹션 → 청크 → 색인."""

from __future__ import annotations

from dataclasses import dataclass

from peerlens.edgar.client import EdgarClient
from peerlens.edgar.filings import FilingRef, fetch_filing_html, html_to_blocks, latest_annual_filing, split_sections
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


def build_chunks(client: EdgarClient, ticker: str) -> tuple[FilingRef, list[Chunk]]:
    ref = latest_annual_filing(client, ticker)
    html, retrieved_at = fetch_filing_html(client, ref)
    sections = split_sections(html_to_blocks(html), ref.form)
    chunks = [c for sec in sections.values() for c in chunk_section(ref, sec, retrieved_at)]
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
            by_sec[c.section] = by_sec.get(c.section, 0) + 1
        if not chunks:
            reports.append(IndexReport(t.upper(), ref, by_sec, error="섹션을 찾지 못함 (비표준 공시 형식)"))
            continue
        added, skipped = index.index_chunks(chunks)
        reports.append(IndexReport(t.upper(), ref, by_sec, added, skipped))
    return reports
