"""수치 참조([[...]])와 근거 참조(E번호) 관리.

원칙 1(수치는 LLM이 만들지 않는다)을 구조로 보장하는 곳:
- Writer에게는 '참조 ID → 값' 목록만 주고, 문장에는 [[ID]]만 쓰게 한다.
- 실제 숫자는 render 단계에서 코드가 metrics 계산값으로 채운다.
- 자리표시자 밖에 숫자가 있으면 Verifier가 실패 처리한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER = re.compile(r"\[\[([A-Za-z0-9_.:\-]+)\]\]")
# 숫자처럼 보이지만 수치 주장이 아닌 표기 (공시 양식·회계연도·섹션 번호)
# \b는 한글 조사("Item 1A에")를 단어 문자로 봐서 경계로 인식하지 못하므로 영숫자만 기준으로 경계를 잡는다
_ALLOWED_NUMERIC = re.compile(
    r"(?<![A-Za-z0-9])(?:10-K|10-Q|20-F|40-F|8-K|(?:FY|CY)\d{4}|\d{4}년|Item\s+\d{1,2}[A-D]?|Q[1-4])(?![A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass
class MetricRef:
    ref_id: str  # 예: NVDA.operating_margin.2025, PEER.operating_margin.2025, DIFF.operating_margin.2025
    label: str
    value: float | None
    display: str
    metric_ids: list[str]  # 근거가 되는 calc 지표 ID (출처 패널 연결용)
    formula: str


@dataclass
class EvidenceRef:
    ref_id: str  # E1, E2 …
    question: str
    hit: dict[str, Any]


@dataclass
class RefTable:
    metrics: dict[str, MetricRef] = field(default_factory=dict)
    evidence: dict[str, EvidenceRef] = field(default_factory=dict)

    def add_evidence(self, question: str, hit: dict[str, Any]) -> EvidenceRef | None:
        """같은 청크는 한 번만 등록."""
        if any(e.hit["chunk_id"] == hit["chunk_id"] for e in self.evidence.values()):
            return None
        ref = EvidenceRef(f"E{len(self.evidence) + 1}", question, hit)
        self.evidence[ref.ref_id] = ref
        return ref


def _pct(v: float | None) -> str:
    return "데이터 없음" if v is None else f"{v * 100:.1f}%"


def _pp(v: float | None) -> str:
    if v is None:
        return "데이터 없음"
    return f"{'+' if v >= 0 else '−'}{abs(v) * 100:.1f}%p"


def build_metric_refs(comparison: dict[str, Any]) -> dict[str, MetricRef]:
    """service.compare 결과 → 참조 목록. 회사별 값, Peer 중앙값, 대상−중앙값 차이."""
    refs: dict[str, MetricRef] = {}
    label = {m["name"]: m["label"] for m in comparison["metrics"]}
    formula = {m["name"]: m["formula"] for m in comparison["metrics"]}
    yl = comparison["year_label"]
    for c in comparison["cells"]:
        rid = f"{c['ticker']}.{c['metric']}.{c['year']}"
        refs[rid] = MetricRef(rid, f"{c['ticker']} {label[c['metric']]} {yl}{c['year']}", c["value"], _pct(c["value"]),
                              [c["metric_id"]], formula[c["metric"]])
    target = comparison["target"]
    for p in comparison["peer_median"]:
        rid = f"PEER.{p['metric']}.{p['year']}"
        refs[rid] = MetricRef(rid, f"Peer 중앙값 {label[p['metric']]} {yl}{p['year']} (n={p['n']})", p["median"],
                              _pct(p["median"]), [], p["formula"])
        t = refs.get(f"{target}.{p['metric']}.{p['year']}")
        if t is not None and t.value is not None and p["median"] is not None:
            did = f"DIFF.{p['metric']}.{p['year']}"
            refs[did] = MetricRef(did, f"{target} − Peer 중앙값 {label[p['metric']]} {yl}{p['year']}",
                                  t.value - p["median"], _pp(t.value - p["median"]), t.metric_ids,
                                  f"{target} {p['metric']} − median(peers)")
    return refs


def stray_numbers(text: str) -> list[str]:
    """자리표시자·허용 표기를 지운 뒤 남은 숫자 (= LLM이 직접 쓴 수치)."""
    rest = _ALLOWED_NUMERIC.sub(" ", PLACEHOLDER.sub(" ", text))
    return re.findall(r"\d[\d,.]*\s*%?", rest)


def render_text(text: str, refs: RefTable) -> tuple[str, list[dict[str, Any]]]:
    """[[ID]] → 값. (렌더된 문장, 사용한 참조 목록)"""
    used: list[dict[str, Any]] = []

    def sub(m: re.Match[str]) -> str:
        r = refs.metrics.get(m.group(1))
        if r is None:
            return f"⟦{m.group(1)}?⟧"
        used.append({"ref_id": r.ref_id, "label": r.label, "display": r.display, "metric_ids": r.metric_ids, "formula": r.formula})
        return r.display

    return PLACEHOLDER.sub(sub, text), used


def segments(text: str, refs: RefTable) -> list[dict[str, Any]]:
    """화면용: 문장을 [일반 텍스트 | 수치 참조] 조각으로 나눈다 (수치 클릭 → 출처 패널)."""
    out: list[dict[str, Any]] = []
    pos = 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > pos:
            out.append({"type": "text", "text": text[pos : m.start()]})
        r = refs.metrics.get(m.group(1))
        out.append(
            {"type": "metric", "ref_id": m.group(1), "text": r.display if r else "?", "label": r.label if r else "알 수 없는 참조",
             "metric_ids": r.metric_ids if r else [], "formula": r.formula if r else ""}
        )
        pos = m.end()
    if pos < len(text):
        out.append({"type": "text", "text": text[pos:]})
    return out
