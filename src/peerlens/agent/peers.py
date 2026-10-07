"""find_peers: 업종코드(SIC) + 사업설명 임베딩 유사도 + 매출 규모로 Peer 후보를 고른다.

SIC만 쓰면 놓치는 Peer가 있다 (예: QCOM은 SIC 3663 통신장비라 반도체 3674와 다름).
그래서 10-K 'Business' 섹션 앞부분을 임베딩해 실제 사업 내용이 비슷한 기업을 함께 본다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from peerlens.config import Settings
from peerlens.edgar.client import EdgarClient
from peerlens.edgar.filings import fetch_filing_html, html_to_blocks, latest_annual_filing, split_sections
from peerlens.retrieval.providers import Embedder

# 후보군 (MVP 30~50개사로 확장 예정). 반도체·장비·빅테크
UNIVERSE = [
    "NVDA", "AMD", "INTC", "AVGO", "QCOM", "TSM", "ARM", "MU", "TXN", "ADI", "MRVL", "NXPI", "ON", "MCHP",
    "AMAT", "LRCX", "KLAC", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "ORCL", "CSCO", "IBM",
]
PROFILE_CHARS = 6000
W_SIM, W_SIC, W_SIZE = 0.65, 0.2, 0.15


@dataclass
class Profile:
    ticker: str
    name: str
    sic: str
    sic_desc: str
    revenue: float | None  # 최신 연간 매출 (USD 환산 아님, 보고통화)
    currency: str | None
    business_excerpt: str
    embedding: list[float]
    embed_model: str


@dataclass
class PeerCandidate:
    ticker: str
    name: str
    score: float
    similarity: float
    sic: str
    sic_desc: str
    sic_match: str  # "동일" | "유사(상위 2자리)" | "다름"
    size_ratio: float | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class PeerFinder:
    def __init__(self, client: EdgarClient, embedder: Embedder, settings: Settings | None = None):
        self.client = client
        self.embedder = embedder
        self.dir = (settings or client.settings).cache_dir / "peer_profiles"

    def profile(self, ticker: str) -> Profile | None:
        ticker = ticker.upper()
        path = self.dir / f"{ticker}.json"
        if path.exists():
            p = Profile(**json.loads(path.read_text(encoding="utf-8")))
            if p.embed_model == self.embedder.model:
                return p
        try:
            p = self._build(ticker)
        except Exception:
            return None
        if p:
            self.dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(p)), encoding="utf-8")
        return p

    def _build(self, ticker: str) -> Profile | None:
        from peerlens import service  # 순환 import 방지

        cik, name = self.client.resolve_ticker(ticker)
        sub = self.client.submissions(cik).data
        ref = latest_annual_filing(self.client, ticker)
        html, _ = fetch_filing_html(self.client, ref)
        secs = split_sections(html_to_blocks(html), ref.form)
        biz = secs.get("business")
        if biz is None:
            return None
        text = ""
        for b in biz.blocks:
            if not b.in_table:
                text += b.text + "\n"
            if len(text) >= PROFILE_CHARS:
                break
        vec = self.embedder.embed([f"{name} business overview\n{text[:PROFILE_CHARS]}"], "search_document")[0]
        rev = next((f for f in sorted(service.company_facts(ticker), key=lambda f: f.period_end, reverse=True) if f.concept == "revenue"), None)
        return Profile(
            ticker=ticker, name=sub.get("name", name), sic=str(sub.get("sic", "")), sic_desc=sub.get("sicDescription", ""),
            revenue=rev.value if rev else None, currency=rev.unit if rev else None,
            business_excerpt=text[:600], embedding=vec, embed_model=self.embedder.model,
        )

    def find(self, target: str, *, k: int = 5, universe: list[str] | None = None, exclude: list[str] | None = None) -> list[PeerCandidate]:
        tp = self.profile(target)
        if tp is None:
            raise LookupError(f"{target}: 사업설명(10-K Business)을 찾지 못해 Peer를 고를 수 없음")
        skip = {target.upper(), *(x.upper() for x in exclude or [])}
        out: list[PeerCandidate] = []
        for t in universe or UNIVERSE:
            if t in skip:
                continue
            p = self.profile(t)
            if p is None:
                continue
            sim = _cos(tp.embedding, p.embedding)
            if p.sic == tp.sic:
                sic_s, sic_m = 1.0, "동일"
            elif p.sic[:3] == tp.sic[:3]:
                sic_s, sic_m = 0.7, "유사(상위 3자리)"
            elif p.sic[:2] == tp.sic[:2]:
                sic_s, sic_m = 0.5, "유사(상위 2자리)"
            else:
                sic_s, sic_m = 0.0, "다름"
            ratio = None
            size_s = 0.5  # 통화가 달라 비교할 수 없으면 중립
            if tp.revenue and p.revenue and tp.currency == p.currency:
                ratio = p.revenue / tp.revenue
                size_s = max(0.0, 1 - abs(math.log10(ratio)) / 2)  # 100배 차이면 0
            score = W_SIM * sim + W_SIC * sic_s + W_SIZE * size_s
            size_txt = f"매출 규모 {ratio:.2f}배" if ratio is not None else "매출 규모 비교 불가(통화 상이)"
            reason = f"사업설명 유사도 {sim:.2f} · SIC {p.sic}({p.sic_desc}) {sic_m} · {size_txt}"
            out.append(PeerCandidate(t, p.name, round(score, 4), round(sim, 4), p.sic, p.sic_desc, sic_m, ratio, reason))
        out.sort(key=lambda c: -c.score)
        return out[:k]
