"""최신 실적: TTM(최근 12개월)과 최근 분기 지표.

연간(10-K)만 쓰면 결산 후 최대 1년 가까이 묵은 숫자가 된다. 분기 공시(10-Q·6-K) 원값으로
- TTM 손익 = 최근 연간 + 올해 누적(YTD) − 작년 같은 기간 누적  (세 값 모두 공시 원값)
- 최근 분기 = 최근 3개월 값, 전년 동기 대비 성장률
- 최근 분기가 4분기면(10-K가 최신) 4분기 = 연간 − 9개월 누적 (계산값 플래그)
을 계산한다. 모든 값에 계산식과 입력 fact_id를 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from peerlens.metrics.calc import METRICS, MetricValue
from peerlens.metrics.facts import Fact

FLOW = ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "rnd_expense",
        "operating_cash_flow", "capex")
YEAR = timedelta(days=364)
TOL = 10  # 52/53주 회계연도 차이 허용 (일)


@dataclass
class PV:
    """기간 값: 값 + 그 값을 만든 원값들 + 계산 방식."""
    value: float
    inputs: list[Fact]
    how: str
    derived: bool = False
    signs: list[int] | None = None  # inputs 각각의 부호 (TTM: +연간 +올해누적 −작년누적). 없으면 모두 +

    def terms(self) -> list[list]:
        signs = self.signs or [1] * len(self.inputs)
        return [[sg, f.fact_id] for sg, f in zip(signs, self.inputs)]


@dataclass
class PeriodSet:
    kind: str  # "TTM" | "Q"
    end: str
    label: str
    fiscal_year: int
    values: dict[str, PV] = field(default_factory=dict)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _near(a: str, b: date, tol: int = TOL) -> bool:
    return abs((_d(a) - b).days) <= tol


class _Index:
    def __init__(self, facts: list[Fact]):
        self.annual = [f for f in facts if f.annual]
        self.interim = [f for f in facts if not f.annual]
        self.fy_ends = sorted({f.period_end for f in self.annual if f.concept == "revenue"})

    def fy(self, concept: str, end: str) -> Fact | None:
        return next((f for f in self.annual if f.concept == concept and f.period_end == end and f.period_start), None)

    def last_fy_end_before(self, d: date) -> str | None:
        prior = [e for e in self.fy_ends if _d(e) < d]
        return prior[-1] if prior else None

    def ytd(self, concept: str, fy_end: str, end: date) -> Fact | None:
        """fy_end 다음 날부터 end까지의 누적값 (분기 공시)."""
        start = _d(fy_end) + timedelta(days=1)
        return next((f for f in self.interim if f.concept == concept and f.period_start and _near(f.period_start, start, 3)
                     and _near(f.period_end, end)), None)

    def quarter(self, concept: str, end: date) -> Fact | None:
        cands = [f for f in self.interim if f.concept == concept and f.days and f.days <= 100 and _near(f.period_end, end)]
        return min(cands, key=lambda f: abs((_d(f.period_end) - end).days)) if cands else None

    def instant(self, concept: str, end: date) -> Fact | None:
        cands = [f for f in self.annual + self.interim if f.concept == concept and not f.period_start and _near(f.period_end, end)]
        return min(cands, key=lambda f: abs((_d(f.period_end) - end).days)) if cands else None

    def latest_interim_end(self) -> str | None:
        ends = [f.period_end for f in self.interim if f.concept == "revenue" and f.period_start]
        return max(ends) if ends else None


def _ttm_flow(ix: _Index, concept: str, end: date) -> PV | None:
    fy_end = ix.last_fy_end_before(end + timedelta(days=1))
    if fy_end is None:
        return None
    if _near(fy_end, end, 3):  # 기준일이 회계연도 말이면 연간값 그대로
        f = ix.fy(concept, fy_end)
        return PV(f.value, [f], "연간") if f else None
    fy, ytd = ix.fy(concept, fy_end), ix.ytd(concept, fy_end, end)
    if fy is None or ytd is None:
        return None
    prev_fy_end = ix.last_fy_end_before(_d(fy_end))
    prior = ix.ytd(concept, prev_fy_end, end - YEAR) if prev_fy_end else None
    if prior is None:
        return None
    return PV(fy.value + ytd.value - prior.value, [fy, ytd, prior], "연간 + 올해 누적 − 작년 같은 기간 누적", signs=[1, 1, -1])


def ttm_set(ix: _Index, end: str) -> PeriodSet:
    e = _d(end)
    fy_end = ix.last_fy_end_before(e + timedelta(days=1))
    fyear = next((f.fiscal_year for f in ix.interim + ix.annual if f.period_end == end), e.year)
    on_fy_end = bool(fy_end) and _near(fy_end, e, 3)
    ps = PeriodSet("TTM", end, f"FY{fyear} 연간 (분기 데이터 없음)" if on_fy_end else f"TTM ~{end}", fyear)
    for c in FLOW:
        if (pv := _ttm_flow(ix, c, e)) is not None:
            ps.values[c] = pv
    for c, at in (("equity", e), ("equity_prev", e - YEAR)):
        if (f := ix.instant("equity", at)) is not None:
            ps.values[c] = PV(f.value, [f], "기말 잔액")
    if fy_end and not _near(fy_end, e, 3):
        prev = _ttm_flow(ix, "revenue", e - YEAR)
        if prev is not None:
            ps.values["revenue_prev"] = prev
    elif (prev_end := ix.last_fy_end_before(e)) and (f := ix.fy("revenue", prev_end)):
        ps.values["revenue_prev"] = PV(f.value, [f], "연간")
    return ps


def _quarter_flow(ix: _Index, concept: str, end: date) -> PV | None:
    q = ix.quarter(concept, end)
    if q is not None:
        return PV(q.value, [q], "분기")
    fy_end = ix.last_fy_end_before(end + timedelta(days=1))
    if fy_end and _near(fy_end, end, 3):  # 4분기 = 연간 − 9개월 누적
        fy = ix.fy(concept, fy_end)
        prev_fy_end = ix.last_fy_end_before(_d(fy_end))
        nine = next((f for f in ix.interim if f.concept == concept and prev_fy_end and f.period_start
                     and _near(f.period_start, _d(prev_fy_end) + timedelta(days=1), 3) and f.days and 260 <= f.days <= 285), None)
        if fy and nine:
            return PV(fy.value - nine.value, [fy, nine], "연간 − 9개월 누적", derived=True, signs=[1, -1])
    return None


def quarter_set(ix: _Index, end: str) -> PeriodSet:
    e = _d(end)
    fy_end = ix.last_fy_end_before(e)
    ytd = ix.ytd("revenue", fy_end, e) if fy_end else None
    n = 4 if ytd is None else max(1, round((ytd.days or 91) / 91))
    fyear = ytd.fiscal_year if ytd else next((f.fiscal_year for f in ix.annual if f.period_end == end), e.year)
    ps = PeriodSet("Q", end, f"FY{fyear} Q{n}", fyear)
    for c in ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income"):
        if (pv := _quarter_flow(ix, c, e)) is not None:
            ps.values[c] = pv
    if (prev := _quarter_flow(ix, "revenue", e - YEAR)) is not None:
        ps.values["revenue_prev"] = prev
    return ps


def latest_end(facts: list[Fact]) -> str | None:
    ix = _Index(facts)
    cands = [x for x in (ix.latest_interim_end(), ix.fy_ends[-1] if ix.fy_ends else None) if x]
    return max(cands) if cands else None


# ---- 지표 ----------------------------------------------------------------------------------

def _metric(ticker: str, ps: PeriodSet, name: str, value: float | None, parts: list[PV], formula: str, explain: str,
            extra_flags: list[str] | None = None, keys: list[str] | None = None) -> MetricValue:
    inputs: list[str] = []
    flags = list(extra_flags or [])
    for pv in parts:
        if pv.derived:
            flags.append("derived:q4=연간−9개월 누적")
        for f in pv.inputs:
            if f.fact_id not in inputs:
                inputs.append(f.fact_id)
            if not f.exact_tag:
                flags.append(f"proxy_tag:{f.concept}={f.tag}")
            if f.restated:
                flags.append(f"restated:{f.fact_id}")
    if value is None:
        flags.append("missing_input")
    e = _d(ps.end)
    return MetricValue(
        metric_id=f"{ticker}:{name}:{ps.kind}:{ps.end}", ticker=ticker, metric=name, label_ko=METRICS[name].label_ko,
        value=value, fiscal_year=ps.fiscal_year, calendar_year=(e - timedelta(days=182)).year, period_end=ps.end,
        formula=formula, inputs=inputs, flags=list(dict.fromkeys(flags)), period_type=ps.kind, period_label=ps.label,
        explain=explain,
        components={k: pv.terms() for k, pv in zip(keys or [], parts)} or None,
    )


def _fmt(pv: PV) -> str:
    unit = pv.inputs[0].unit
    sym = {"USD": "$", "EUR": "€", "TWD": "NT$"}.get(unit, "")
    return f"{sym}{pv.value / 1e6:,.0f}M"


def _ratio(ticker: str, ps: PeriodSet, name: str, num: str, den: str, sfx: str) -> MetricValue | None:
    a, b = ps.values.get(num), ps.values.get(den)
    if b is None:
        return None
    v = None if a is None or b.value == 0 else a.value / b.value
    parts = [x for x in (a, b) if x]
    explain = f"{num}{sfx} {_fmt(a) if a else '없음'} ÷ {den}{sfx} {_fmt(b)}"
    return _metric(ticker, ps, name, v, parts, f"{num}{sfx} / {den}{sfx}", explain, keys=[k for k, x in ((num, a), (den, b)) if x])


def compute_recent_metrics(facts: list[Fact]) -> list[MetricValue]:
    """기업 하나의 TTM·최근 분기 지표. 분기 공시가 없으면 연간 기준(TTM = 최근 연간)."""
    if not facts:
        return []
    ticker = facts[0].ticker
    ix = _Index(facts)
    end = latest_end(facts)
    if end is None:
        return []
    out: list[MetricValue] = []

    t = ttm_set(ix, end)
    sfx = "_TTM"
    for name, num in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income"),
                      ("net_margin", "net_income"), ("rnd_intensity", "rnd_expense")):
        if name == "gross_margin" and "gross_profit" not in t.values and {"revenue", "cost_of_revenue"} <= t.values.keys():
            r, c = t.values["revenue"], t.values["cost_of_revenue"]
            out.append(_metric(ticker, t, name, (r.value - c.value) / r.value, [r, c], "(revenue_TTM - cost_of_revenue_TTM) / revenue_TTM",
                               f"(매출 {_fmt(r)} − 매출원가 {_fmt(c)}) ÷ 매출 {_fmt(r)}", ["derived:gross_profit=revenue-cost_of_revenue"],
                               keys=["revenue", "cost_of_revenue"]))
            continue
        if (m := _ratio(ticker, t, name, num, "revenue", sfx)) is not None:
            out.append(m)
    if {"operating_cash_flow", "capex", "revenue"} <= t.values.keys():
        o, c, r = t.values["operating_cash_flow"], t.values["capex"], t.values["revenue"]
        out.append(_metric(ticker, t, "fcf_margin", (o.value - c.value) / r.value, [o, c, r],
                           "(operating_cash_flow_TTM - capex_TTM) / revenue_TTM", f"(영업현금흐름 {_fmt(o)} − CAPEX {_fmt(c)}) ÷ 매출 {_fmt(r)}",
                           keys=["operating_cash_flow", "capex", "revenue"]))
    if "net_income" in t.values and "equity" in t.values:
        ni, eq = t.values["net_income"], t.values["equity"]
        prev = t.values.get("equity_prev")
        denom = (eq.value + prev.value) / 2 if prev else eq.value
        flags = [] if prev else ["ending_equity_only"]
        v = ni.value / denom if denom > 0 else None
        if denom <= 0:
            flags.append("non_positive_equity")
        out.append(_metric(ticker, t, "roe", v, [x for x in (ni, eq, prev) if x],
                           "net_income_TTM / avg(equity[t], equity[t-1y])" if prev else "net_income_TTM / equity[t]",
                           f"순이익 TTM {_fmt(ni)} ÷ 평균자본 {_fmt(PV(denom, eq.inputs, ''))}", flags,
                           keys=["net_income", "equity", "equity_prev"][: 3 if prev else 2]))
    if "revenue" in t.values and "revenue_prev" in t.values:
        r, p = t.values["revenue"], t.values["revenue_prev"]
        out.append(_metric(ticker, t, "revenue_growth", r.value / p.value - 1 if p.value else None, [r, p],
                           "revenue_TTM / revenue_TTM[1년 전] - 1", f"매출 TTM {_fmt(r)} ÷ 1년 전 TTM {_fmt(p)} − 1",
                           keys=["revenue", "revenue_prev"]))

    q = quarter_set(ix, end)
    sfx = "_Q"
    for name, num in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income"), ("net_margin", "net_income")):
        if name == "gross_margin" and "gross_profit" not in q.values and {"revenue", "cost_of_revenue"} <= q.values.keys():
            r, c = q.values["revenue"], q.values["cost_of_revenue"]
            out.append(_metric(ticker, q, name, (r.value - c.value) / r.value, [r, c], "(revenue_Q - cost_of_revenue_Q) / revenue_Q",
                               f"(매출 {_fmt(r)} − 매출원가 {_fmt(c)}) ÷ 매출 {_fmt(r)}", ["derived:gross_profit=revenue-cost_of_revenue"],
                               keys=["revenue", "cost_of_revenue"]))
            continue
        if (m := _ratio(ticker, q, name, num, "revenue", sfx)) is not None:
            out.append(m)
    if "revenue" in q.values and "revenue_prev" in q.values:
        r, p = q.values["revenue"], q.values["revenue_prev"]
        out.append(_metric(ticker, q, "revenue_growth", r.value / p.value - 1 if p.value else None, [r, p],
                           "revenue_Q / revenue_Q[전년 동기] - 1", f"분기 매출 {_fmt(r)} ÷ 전년 동기 {_fmt(p)} − 1",
                           keys=["revenue", "revenue_prev"]))
    return out


def recent_scale(facts: list[Fact]) -> dict | None:
    """규모 비교용 TTM 매출 (보고 통화 그대로)."""
    end = latest_end(facts)
    if end is None:
        return None
    t = ttm_set(_Index(facts), end)
    r = t.values.get("revenue")
    if r is None:
        return None
    return {"value": r.value, "unit": r.inputs[0].unit, "label": t.label, "period_end": end, "inputs": [f.fact_id for f in r.inputs],
            "how": r.how, "terms": r.terms()}
