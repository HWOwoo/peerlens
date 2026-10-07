import math

import pytest

from peerlens.metrics.calc import comparison_table, compute_metrics
from peerlens.metrics.facts import calendar_year_of, extract_facts, fiscal_year_label, reporting_currency
from datetime import date


def row(val, start, end, *, filed, form="10-K", fp="FY", accn=None):
    r = {"val": val, "end": end, "form": form, "fp": fp, "filed": filed, "accn": accn or f"0000-{filed}"}
    if start:
        r["start"] = start
    return r


def companyfacts(gaap: dict, *, ifrs: dict | None = None, cik=1, name="TestCo"):
    facts = {"us-gaap": {tag: {"units": units} for tag, units in gaap.items()}}
    if ifrs:
        facts["ifrs-full"] = {tag: {"units": units} for tag, units in ifrs.items()}
    return {"cik": cik, "entityName": name, "facts": facts}


FY = {  # 52/53주 회계연도, 1월 말 결산 (NVDA형)
    2023: ("2022-01-31", "2023-01-29"),
    2024: ("2023-01-30", "2024-01-28"),
    2025: ("2024-01-29", "2025-01-26"),
}


def test_fiscal_and_calendar_labels():
    assert fiscal_year_label(date(2025, 1, 26)) == 2025
    assert fiscal_year_label(date(2023, 1, 1)) == 2022  # 1월 첫 주 종료는 전년도 회계연도
    assert calendar_year_of(date(2025, 1, 26)) == 2024
    assert calendar_year_of(date(2025, 9, 28)) == 2025
    assert calendar_year_of(date(2025, 11, 2)) == 2025


def test_period_filter_restatement_and_tag_fallback():
    cf = companyfacts({
        # 구 태그: FY2023까지만
        "SalesRevenueNet": {"USD": [row(100.0, *FY[2023], filed="2023-03-01")]},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"USD": [
            row(200.0, *FY[2024], filed="2024-03-01"),
            row(210.0, *FY[2024], filed="2025-03-01"),          # 다음 해 10-K에서 재작성
            row(300.0, *FY[2025], filed="2025-03-01"),
            row(90.0, "2024-10-28", "2025-01-26", filed="2025-03-01"),  # 10-K 안의 4분기 값 → 제외
            row(70.0, "2024-07-29", "2024-10-27", filed="2024-11-20", form="10-Q", fp="Q3"),
        ]},
    })
    facts = {f.fiscal_year: f for f in extract_facts(cf, ticker="T", concepts=["revenue"])}
    assert sorted(facts) == [2023, 2024, 2025]
    assert facts[2023].tag == "SalesRevenueNet"
    assert facts[2024].value == 210.0 and facts[2024].restated and facts[2024].original_value == 200.0
    assert facts[2025].value == 300.0 and not facts[2025].restated
    assert facts[2025].filing_url.endswith("/0000-2025-03-01-index.htm")


def test_instant_values_only_at_fiscal_year_ends_and_proxy_flag():
    cf = companyfacts({
        "NetIncomeLoss": {"USD": [row(10.0, *FY[2024], filed="2024-03-01"), row(20.0, *FY[2025], filed="2025-03-01")]},
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"USD": [
            row(100.0, None, FY[2024][1], filed="2024-03-01"),
            row(120.0, None, FY[2025][1], filed="2025-03-01"),
            row(999.0, None, "2024-07-28", filed="2025-03-01"),  # 회계연도 말이 아닌 instant → 제외
        ]},
    })
    eq = extract_facts(cf, ticker="T", concepts=["equity"])
    assert [f.value for f in eq] == [100.0, 120.0]
    assert all(not f.exact_tag for f in eq)


def test_reporting_currency_prefers_primary_over_convenience_translation():
    ifrs = {"Revenue": {
        "TWD": [row(v, f"{y}-01-01", f"{y}-12-31", filed=f"{y + 1}-04-01", form="20-F") for y, v in [(2023, 1.0), (2024, 2.0)]],
        "USD": [row(0.06, "2024-01-01", "2024-12-31", filed="2025-04-01", form="20-F")],
    }}
    cf = companyfacts({}, ifrs=ifrs)
    assert reporting_currency(cf["facts"]) == "TWD"
    assert {f.unit for f in extract_facts(cf, ticker="TSM", concepts=["revenue"])} == {"TWD"}


def _facts_for_metrics(gross_profit: bool = True, equity_prev: bool = True, gap_year: bool = False):
    years = [2023, 2025] if gap_year else [2023, 2024, 2025]
    rev = {2023: 100.0, 2024: 150.0, 2025: 300.0}
    g = {
        "Revenues": {"USD": [row(rev[y], *FY[y], filed=f"{y}-03-01") for y in years]},
        "CostOfRevenue": {"USD": [row(rev[y] * 0.4, *FY[y], filed=f"{y}-03-01") for y in years]},
        "OperatingIncomeLoss": {"USD": [row(rev[y] * 0.3, *FY[y], filed=f"{y}-03-01") for y in years]},
        "NetIncomeLoss": {"USD": [row(rev[y] * 0.2, *FY[y], filed=f"{y}-03-01") for y in years]},
        "StockholdersEquity": {"USD": [row(100.0 * (i + 1), None, FY[y][1], filed=f"{y}-03-01")
                                       for i, y in enumerate(years) if equity_prev or y == 2025]},
    }
    if gross_profit:
        g["GrossProfit"] = {"USD": [row(rev[y] * 0.6, *FY[y], filed=f"{y}-03-01") for y in years]}
    return extract_facts(companyfacts(g), ticker="T")


def _metric(metrics, name, fy):
    return next(m for m in metrics if m.metric == name and m.fiscal_year == fy)


def test_compute_metrics_values_and_provenance():
    m = compute_metrics(_facts_for_metrics())
    growth = _metric(m, "revenue_growth", 2025)
    assert growth.value == pytest.approx(1.0)
    assert growth.inputs == ["T:revenue:2025-01-26", "T:revenue:2024-01-28"]
    assert _metric(m, "gross_margin", 2025).value == pytest.approx(0.6)
    assert _metric(m, "operating_margin", 2025).value == pytest.approx(0.3)
    roe = _metric(m, "roe", 2025)
    assert roe.value == pytest.approx(60.0 / 250.0)  # 평균자본 (200+300)/2
    assert roe.flags == []
    assert _metric(m, "revenue_growth", 2023).value is None


def test_gross_margin_derived_and_ending_equity_fallback():
    m = compute_metrics(_facts_for_metrics(gross_profit=False, equity_prev=False))
    gm = _metric(m, "gross_margin", 2025)
    assert gm.value == pytest.approx(0.6)
    assert any(f.startswith("derived:") for f in gm.flags)
    roe = _metric(m, "roe", 2025)
    assert roe.value == pytest.approx(60.0 / 300.0) and "ending_equity_only" in roe.flags


def test_growth_not_computed_across_missing_year():
    m = compute_metrics(_facts_for_metrics(gap_year=True))
    g = _metric(m, "revenue_growth", 2025)
    assert g.value is None and "missing:prior_year_revenue" in g.flags


def test_comparison_table_uses_majority_latest_year():
    a = _facts_for_metrics()
    b = [f for f in _facts_for_metrics() if f.fiscal_year < 2025]
    c = _facts_for_metrics()
    import dataclasses
    facts = [dataclasses.replace(f, ticker=t, fact_id=f.fact_id.replace("T:", f"{t}:", 1))
             for t, fl in [("A", a), ("B", b), ("C", c)] for f in fl]
    table = comparison_table(compute_metrics(facts), years=2, align="fiscal")
    row_ = table.loc[("매출 성장률(YoY)", "FY2025")]
    assert row_["A"] == pytest.approx(1.0) and math.isnan(row_["B"])
