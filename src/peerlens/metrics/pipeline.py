"""티커 목록 → Fact·지표. Agent의 get_financials / calc_metrics Tool이 이 함수를 감싼다."""

from __future__ import annotations

from dataclasses import dataclass

from peerlens.edgar.client import EdgarClient
from peerlens.metrics.calc import MetricValue, compute_metrics
from peerlens.metrics.facts import Fact, extract_facts


@dataclass
class PeerDataset:
    tickers: list[str]
    facts: list[Fact]
    metrics: list[MetricValue]

    def fact(self, fact_id: str) -> Fact:
        return next(f for f in self.facts if f.fact_id == fact_id)


def load_facts(client: EdgarClient, ticker: str) -> list[Fact]:
    cik, _ = client.resolve_ticker(ticker)
    resp = client.company_facts(cik)
    return extract_facts(resp.data, ticker=ticker.upper(), api_url=resp.url, retrieved_at=resp.retrieved_at)


def build_peer_dataset(client: EdgarClient, tickers: list[str]) -> PeerDataset:
    tickers = [t.upper() for t in tickers]
    facts = [f for t in tickers for f in load_facts(client, t)]
    return PeerDataset(tickers, facts, compute_metrics(facts))
