"""calc_metrics: 원 수치(Fact) → 비율 지표. 모든 값에 계산식과 입력 fact_id를 남긴다.

LLM은 이 모듈이 만든 값만 인용할 수 있다 (원칙 1: 수치는 LLM이 만들지 않는다).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Literal

import pandas as pd

from peerlens.metrics.facts import ANNUAL_DAYS, Fact


@dataclass(frozen=True)
class MetricDef:
    name: str
    label_ko: str
    formula: str


METRICS: dict[str, MetricDef] = {
    m.name: m
    for m in [
        MetricDef("revenue_growth", "매출 성장률(YoY)", "revenue[t] / revenue[t-1] - 1"),
        MetricDef("gross_margin", "매출총이익률", "gross_profit / revenue"),
        MetricDef("operating_margin", "영업이익률", "operating_income / revenue"),
        MetricDef("net_margin", "순이익률", "net_income / revenue"),
        MetricDef("roe", "ROE", "net_income / avg(equity[t], equity[t-1])"),
        MetricDef("rnd_intensity", "R&D 비중", "rnd_expense / revenue"),
        MetricDef("fcf_margin", "FCF 마진", "(operating_cash_flow - capex) / revenue"),
    ]
}


@dataclass
class MetricValue:
    metric_id: str
    ticker: str
    metric: str
    label_ko: str
    value: float | None
    fiscal_year: int
    calendar_year: int
    period_end: str
    formula: str
    inputs: list[str]
    flags: list[str] = field(default_factory=list)
    period_type: str = "FY"  # FY(연간) | TTM(최근 12개월) | Q(최근 분기)
    period_label: str = ""
    explain: str | None = None  # 실제 값을 대입한 계산식 (TTM·분기)
    components: dict | None = None  # 항목별 [부호, fact_id] 목록 — 평가 시 원본에서 독립 재계산용

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Company:
    """한 회사의 Fact를 기간 종료일 기준으로 조회."""

    def __init__(self, facts: list[Fact]):
        self.by_key = {(f.concept, f.period_end): f for f in facts}
        self.ends = sorted({f.period_end for f in facts if f.concept == "revenue"})

    def get(self, concept: str, end: str | None) -> Fact | None:
        return self.by_key.get((concept, end)) if end else None

    def prev_end(self, end: str) -> str | None:
        """직전 회계연도 종료일 (350~380일 전) — 결측 연도를 건너뛰어 잘못 비교하지 않도록."""
        cur = date.fromisoformat(end)
        candidates = {f.period_end for f in self.by_key.values()}
        for e in sorted(candidates, reverse=True):
            if (cur - date.fromisoformat(e)).days in ANNUAL_DAYS:
                return e
        return None


def _make(
    ticker: str, name: str, rev: Fact, value: float | None, inputs: list[Fact | None], flags: list[str], formula: str | None = None
) -> MetricValue:
    used = [f for f in inputs if f is not None]
    for f in used:
        if not f.exact_tag:
            flags.append(f"proxy_tag:{f.concept}={f.tag}")
        if f.restated:
            flags.append(f"restated:{f.fact_id}")
    if value is None and not any(fl.startswith("missing") for fl in flags):
        flags.append("missing_input")
    flags = list(dict.fromkeys(flags))
    d = METRICS[name]
    return MetricValue(
        metric_id=f"{ticker}:{name}:{rev.period_end}",
        ticker=ticker,
        metric=name,
        label_ko=d.label_ko,
        value=value,
        fiscal_year=rev.fiscal_year,
        calendar_year=rev.calendar_year,
        period_end=rev.period_end,
        formula=formula or d.formula,
        inputs=[f.fact_id for f in used],
        flags=flags,
    )


def _ratio(num: Fact | None, den: Fact | None) -> float | None:
    if num is None or den is None or den.value == 0:
        return None
    return num.value / den.value


def compute_metrics(facts: Iterable[Fact]) -> list[MetricValue]:
    by_ticker: dict[str, list[Fact]] = {}
    for f in facts:
        if f.annual:  # 연간 지표는 연간 공시 값만 (분기 값은 metrics/recent.py)
            by_ticker.setdefault(f.ticker, []).append(f)

    out: list[MetricValue] = []
    for ticker, flist in by_ticker.items():
        c = _Company(flist)
        for end in c.ends:
            rev = c.get("revenue", end)
            assert rev is not None
            prev = c.prev_end(end)
            g = lambda concept, e=end: c.get(concept, e)  # noqa: E731

            prev_rev = g("revenue", prev)
            out.append(_make(ticker, "revenue_growth", rev,
                             None if prev_rev is None or prev_rev.value == 0 else rev.value / prev_rev.value - 1,
                             [rev, prev_rev], [] if prev_rev else ["missing:prior_year_revenue"]))

            gp, cor = g("gross_profit"), g("cost_of_revenue")
            if gp is not None:
                out.append(_make(ticker, "gross_margin", rev, _ratio(gp, rev), [gp, rev], []))
            else:
                val = None if cor is None else (rev.value - cor.value) / rev.value
                out.append(_make(ticker, "gross_margin", rev, val, [rev, cor], ["derived:gross_profit=revenue-cost_of_revenue"],
                                 "(revenue - cost_of_revenue) / revenue"))

            out.append(_make(ticker, "operating_margin", rev, _ratio(g("operating_income"), rev), [g("operating_income"), rev], []))
            out.append(_make(ticker, "net_margin", rev, _ratio(g("net_income"), rev), [g("net_income"), rev], []))
            out.append(_make(ticker, "rnd_intensity", rev, _ratio(g("rnd_expense"), rev), [g("rnd_expense"), rev], []))

            ocf, capex = g("operating_cash_flow"), g("capex")
            fcf = None if ocf is None or capex is None else (ocf.value - capex.value) / rev.value
            out.append(_make(ticker, "fcf_margin", rev, fcf, [ocf, capex, rev], []))

            ni, eq, eq_prev = g("net_income"), g("equity"), g("equity", prev)
            flags: list[str] = []
            formula = None
            if eq is not None and eq_prev is not None:
                denom = (eq.value + eq_prev.value) / 2
            elif eq is not None:
                denom, formula = eq.value, "net_income / equity[t]"
                flags.append("ending_equity_only")
            else:
                denom = None
            if denom is not None and denom <= 0:
                flags.append("non_positive_equity")
                denom = None
            roe = None if ni is None or denom is None else ni.value / denom
            out.append(_make(ticker, "roe", rev, roe, [ni, eq, eq_prev], flags, formula))
    return out


def metrics_frame(metrics: Iterable[MetricValue]) -> pd.DataFrame:
    return pd.DataFrame([m.to_dict() for m in metrics])


Align = Literal["calendar", "fiscal"]


def table_year_col(align: Align) -> str:
    return "calendar_year" if align == "calendar" else "fiscal_year"


def latest_reported_year(df: pd.DataFrame, year_col: str) -> int:
    """과반 회사가 공시를 마친 최신 연도. 한 회사의 공시 지연(예: 20-F 미반영)이 전체 표를 과거로 끌어내리지 않게 한다."""
    n = df["ticker"].nunique()
    counts = df[df["metric"] == "revenue_growth"].groupby(year_col)["ticker"].nunique()
    return int(counts[counts * 2 >= n].index.max())


def comparison_table(metrics: list[MetricValue], *, years: int = 3, align: Align = "calendar") -> pd.DataFrame:
    """(지표, 연도) × 티커 비교표. 과반 회사가 공시한 최신 연도까지 N개 연도를 보여주고, 미공시 칸은 비워 둔다."""
    df = metrics_frame(metrics)
    year_col = table_year_col(align)
    # 같은 연도에 두 기간이 매핑되는 드문 경우 최신 종료일을 쓴다
    df = df.sort_values("period_end").drop_duplicates(["ticker", "metric", year_col], keep="last")
    latest = latest_reported_year(df, year_col)
    df = df[(df[year_col] <= latest) & (df[year_col] > latest - years)]
    order = {m: i for i, m in enumerate(METRICS)}
    table = df.pivot_table(index=["metric", year_col], columns="ticker", values="value", aggfunc="first", dropna=False)
    table = table.reindex(sorted(table.index, key=lambda k: (order[k[0]], -k[1])))
    table.index = pd.MultiIndex.from_tuples(
        [(METRICS[m].label_ko, f"{'CY' if align == 'calendar' else 'FY'}{y}") for m, y in table.index], names=["지표", "연도"]
    )
    return table
