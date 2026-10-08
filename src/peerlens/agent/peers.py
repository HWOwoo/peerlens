"""find_peers: 사업설명 임베딩 유사도 + 세부 업종 + 업종코드(SIC) + 매출 규모로 Peer 후보를 고른다.

SIC만 쓰면 놓치는 Peer가 있다 (예: QCOM은 SIC 3663 통신장비, MU 3674 vs 낸드 SNDK 3572, ASML 3559 vs AMAT 3674).
그래서 10-K 'Business' 섹션 앞부분을 임베딩해 실제 사업 내용이 비슷한 기업을 함께 보고,
후보군의 반도체 가치사슬 세부 업종(GICS 하위 업종과 비슷한 분류)을 더한다.
임베딩 유사도는 값의 폭이 좁아(0.4~0.65) 대상 기업별 후보 안에서 0~1로 다시 맞춘다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from peerlens.config import Settings
from peerlens.edgar.client import EdgarClient
from peerlens.edgar.filings import fetch_filing_html, html_to_blocks, latest_annual_filing, split_sections
from peerlens.retrieval.providers import Embedder

# 후보군 50개사: 세부 업종 → 티커
GROUPS: dict[str, list[str]] = {
    "반도체 설계·IDM": ["NVDA", "AMD", "INTC", "AVGO", "QCOM", "ARM", "TXN", "ADI", "MRVL", "NXPI", "ON", "MCHP",
                      "MPWR", "SWKS", "QRVO", "LSCC", "ALAB", "CRDO", "COHR"],
    "파운드리·후공정": ["TSM", "GFS", "UMC", "AMKR"],
    "메모리·저장장치": ["MU", "WDC", "STX", "SNDK"],
    "반도체 장비·소재": ["ASML", "AMAT", "LRCX", "KLAC", "TER", "ENTG", "ONTO"],
    "EDA": ["SNPS", "CDNS"],
    "AI 인프라·서버·네트워크": ["ANET", "SMCI", "DELL", "HPE", "VRT", "CSCO"],
    "빅테크·소프트웨어": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "ORCL", "IBM", "CRM"],
}
UNIVERSE = [t for ts in GROUPS.values() for t in ts]
GROUP_OF = {t: g for g, ts in GROUPS.items() for t in ts}
# 가치사슬에서 맞닿은 업종 (부분 점수)
RELATED_GROUPS = {
    frozenset(("반도체 설계·IDM", "파운드리·후공정")), frozenset(("반도체 설계·IDM", "메모리·저장장치")),
    frozenset(("파운드리·후공정", "메모리·저장장치")), frozenset(("파운드리·후공정", "반도체 장비·소재")),
    frozenset(("EDA", "반도체 설계·IDM")), frozenset(("AI 인프라·서버·네트워크", "빅테크·소프트웨어")),
}
PROFILE_CHARS = 6000
W_SIM, W_GROUP, W_SIC, W_SIZE = 0.5, 0.25, 0.1, 0.15


def group_score(a: str | None, b: str | None) -> tuple[float, str]:
    if a is None or b is None:
        return 0.0, "후보군 밖"
    if a == b:
        return 1.0, "동일"
    if frozenset((a, b)) in RELATED_GROUPS:
        return 0.5, "인접"
    return 0.0, "다름"


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
    sic_match: str  # "동일" | "유사(상위 3자리)" | "유사(상위 2자리)" | "다름"
    group: str | None  # 후보의 세부 업종
    group_match: str  # "동일" | "인접" | "다름" | "후보군 밖"
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
        profiles = [p for t in (universe or UNIVERSE) if t not in skip and (p := self.profile(t)) is not None]
        sims = {p.ticker: _cos(tp.embedding, p.embedding) for p in profiles}
        lo, hi = min(sims.values(), default=0.0), max(sims.values(), default=1.0)
        tg = GROUP_OF.get(tp.ticker)
        out: list[PeerCandidate] = []
        for p in profiles:
            sim = sims[p.ticker]
            sim_n = (sim - lo) / (hi - lo) if hi > lo else 0.5  # 이 대상의 후보 안에서 0~1
            if p.sic == tp.sic:
                sic_s, sic_m = 1.0, "동일"
            elif p.sic[:3] == tp.sic[:3]:
                sic_s, sic_m = 0.6, "유사(상위 3자리)"
            elif p.sic[:2] == tp.sic[:2]:
                sic_s, sic_m = 0.2, "유사(상위 2자리)"  # 35 = 기계·컴퓨터 전체라 약하게
            else:
                sic_s, sic_m = 0.0, "다름"
            g = GROUP_OF.get(p.ticker)
            grp_s, grp_m = group_score(tg, g)
            ratio = None
            size_s = 0.5  # 통화가 달라 비교할 수 없으면 중립
            if tp.revenue and p.revenue and tp.currency == p.currency:
                ratio = p.revenue / tp.revenue
                size_s = max(0.0, 1 - abs(math.log10(ratio)) / 2)  # 100배 차이면 0
            score = W_SIM * sim_n + W_GROUP * grp_s + W_SIC * sic_s + W_SIZE * size_s
            size_txt = f"매출 규모 {ratio:.2f}배" if ratio is not None else "매출 규모 비교 불가(통화 상이)"
            reason = (f"사업설명 유사도 {sim:.2f}(후보 중 {sim_n:.2f}) · 세부 업종 {g or '–'} {grp_m} · "
                      f"SIC {p.sic}({p.sic_desc}) {sic_m} · {size_txt}")
            out.append(PeerCandidate(p.ticker, p.name, round(score, 4), round(sim, 4), p.sic, p.sic_desc, sic_m,
                                     g, grp_m, ratio, reason))
        out.sort(key=lambda c: -c.score)
        return out[:k]
