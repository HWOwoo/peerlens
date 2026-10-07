"""companyfacts JSON → 연간 재무 사실(Fact) 추출.

주의할 점:
- companyfacts의 `fy` 필드는 '그 값을 담은 공시의' 회계연도다. FY2024 10-K에는 FY2022·2023 비교값도
  fy=2024로 들어 있으므로, 기간은 반드시 `start`/`end` 날짜로 판정한다.
- 연간 공시 안에도 4분기 값 등 1년이 아닌 기간이 섞여 있어, 기간 길이(350~380일)로 거른다.
- 같은 기간 값이 여러 공시에 반복되면 가장 최근 공시 값(재작성 반영)을 쓰고, 최초 값과 다르면 restated로 표시한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd

from peerlens.edgar.client import COMPANYFACTS_URL, filing_index_url
from peerlens.metrics.tags import ANNUAL_FORMS, CONCEPTS, Concept

ANNUAL_DAYS = range(350, 381)  # 52/53주 회계연도 포함


@dataclass(frozen=True)
class Fact:
    fact_id: str
    ticker: str
    cik: int
    company: str
    concept: str
    label_ko: str
    value: float
    unit: str
    fiscal_year: int
    calendar_year: int
    period_start: str | None
    period_end: str
    taxonomy: str
    tag: str
    exact_tag: bool
    form: str
    accn: str
    filed: str
    filing_url: str
    api_url: str
    retrieved_at: str
    restated: bool
    original_value: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fiscal_year_label(end: date) -> int:
    """회계연도 표기: 종료일의 연도. 단, 1월 첫 주에 끝나는 52/53주 연도는 전년도로 본다."""
    if end.month == 1 and end.day <= 7:
        return end.year - 1
    return end.year


def calendar_year_of(end: date) -> int:
    """기간 중간 시점이 속한 달력연도 — 결산월이 다른 Peer끼리 비교할 때 쓴다.
    예) NVDA FY2026(2025-01~2026-01) → CY2025, QCOM FY2025(2024-10~2025-09) → CY2025."""
    return (end - timedelta(days=182)).year


def reporting_currency(facts: dict[str, Any]) -> str:
    """연간 매출 사실이 가장 많은 통화 단위 (TSMC의 USD 편의환산값 등은 제외됨)."""
    counts: Counter[str] = Counter()
    for rule in CONCEPTS["revenue"].tags + CONCEPTS["net_income"].tags:
        units = facts.get(rule.taxonomy, {}).get(rule.tag, {}).get("units", {})
        for unit, rows in units.items():
            if len(unit) == 3 and unit.isupper():
                counts[unit] += sum(1 for r in rows if r.get("form") in ANNUAL_FORMS)
    if not counts:
        raise ValueError("No annual monetary facts found")
    return counts.most_common(1)[0][0]


def _annual_rows(rows: Iterable[dict[str, Any]], kind: str) -> Iterable[dict[str, Any]]:
    for r in rows:
        if r.get("form") not in ANNUAL_FORMS or r.get("fp") != "FY":
            continue
        if kind == "duration":
            if "start" not in r:
                continue
            days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
            if days not in ANNUAL_DAYS:
                continue
        elif "start" in r:
            continue
        yield r


def fiscal_year_ends(facts: dict[str, Any], currency: str) -> set[str]:
    """연간 손익 기간의 종료일 집합. instant 값(자본·자산)은 이 날짜의 값만 쓴다."""
    ends: set[str] = set()
    for name in ("revenue", "net_income", "operating_income"):
        for rule in CONCEPTS[name].tags:
            rows = facts.get(rule.taxonomy, {}).get(rule.tag, {}).get("units", {}).get(currency, [])
            ends.update(r["end"] for r in _annual_rows(rows, "duration"))
    return ends


def extract_concept(
    facts: dict[str, Any],
    concept: Concept,
    *,
    currency: str,
    fy_ends: set[str],
) -> dict[str, tuple[Any, dict[str, Any], list[dict[str, Any]]]]:
    """period_end → (TagRule, 채택 row, 같은 기간의 전체 row). 기간별로 우선순위가 가장 높은 태그를 쓴다."""
    chosen: dict[str, tuple[Any, dict[str, Any], list[dict[str, Any]]]] = {}
    for rule in concept.tags:
        rows = facts.get(rule.taxonomy, {}).get(rule.tag, {}).get("units", {}).get(currency, [])
        by_end: dict[str, list[dict[str, Any]]] = {}
        for r in _annual_rows(rows, concept.kind):
            if concept.kind == "instant" and r["end"] not in fy_ends:
                continue
            by_end.setdefault(r["end"], []).append(r)
        for end, group in by_end.items():
            if end in chosen:
                continue
            group.sort(key=lambda r: (r["filed"], r["accn"]))
            chosen[end] = (rule, group[-1], group)
    return chosen


def extract_facts(
    companyfacts: dict[str, Any],
    *,
    ticker: str,
    api_url: str | None = None,
    retrieved_at: str = "",
    concepts: Iterable[str] | None = None,
) -> list[Fact]:
    cik = int(companyfacts["cik"])
    company = companyfacts.get("entityName", "")
    facts = companyfacts["facts"]
    currency = reporting_currency(facts)
    fy_ends = fiscal_year_ends(facts, currency)
    api_url = api_url or COMPANYFACTS_URL.format(cik=cik)

    out: list[Fact] = []
    for name in concepts or CONCEPTS:
        concept = CONCEPTS[name]
        for end, (rule, row, group) in sorted(extract_concept(facts, concept, currency=currency, fy_ends=fy_ends).items()):
            end_d = date.fromisoformat(end)
            original = group[0]["val"]
            restated = any(r["val"] != row["val"] for r in group)
            out.append(Fact(
                fact_id=f"{ticker}:{name}:{end}",
                ticker=ticker,
                cik=cik,
                company=company,
                concept=name,
                label_ko=concept.label_ko,
                value=float(row["val"]),
                unit=currency,
                fiscal_year=fiscal_year_label(end_d),
                calendar_year=calendar_year_of(end_d),
                period_start=row.get("start"),
                period_end=end,
                taxonomy=rule.taxonomy,
                tag=rule.tag,
                exact_tag=rule.exact,
                form=row["form"],
                accn=row["accn"],
                filed=row["filed"],
                filing_url=filing_index_url(cik, row["accn"]),
                api_url=api_url,
                retrieved_at=retrieved_at,
                restated=restated,
                original_value=float(original) if restated else None,
            ))
    return out


def facts_frame(facts: Iterable[Fact]) -> pd.DataFrame:
    return pd.DataFrame([f.to_dict() for f in facts])
