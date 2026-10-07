"""TTM(최근 12개월)·최근 분기 지표 테스트 — NVDA형 1월 결산 가상 데이터."""

import pytest

from peerlens.metrics.calc import compute_metrics
from peerlens.metrics.facts import extract_facts
from peerlens.metrics.recent import compute_recent_metrics, latest_end, recent_scale


def row(val, start, end, *, form, fp, filed):
    r = {"val": val, "end": end, "form": form, "fp": fp, "filed": filed, "accn": f"A-{filed}-{end}"}
    if start:
        r["start"] = start
    return r


FY2025 = ("2024-01-29", "2025-01-26")
FY2026 = ("2025-01-27", "2026-01-25")


def build(q1_only: bool = False):
    rev = [
        row(100.0, *FY2025, form="10-K", fp="FY", filed="2025-02-20"),
        row(200.0, *FY2026, form="10-K", fp="FY", filed="2026-02-20"),
        # 작년 6개월 누적·2분기, 올해 6개월 누적·2분기
        row(80.0, "2025-01-27", "2025-07-27", form="10-Q", fp="Q2", filed="2025-08-20"),
        row(45.0, "2025-04-28", "2025-07-27", form="10-Q", fp="Q2", filed="2025-08-20"),
        row(150.0, "2026-01-26", "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
        row(90.0, "2026-04-27", "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
        # 1년 전 TTM 계산용: 재작년 6개월 누적
        row(30.0, "2024-01-29", "2024-07-28", form="10-Q", fp="Q2", filed="2024-08-20"),
        row(70.0, "2023-01-30", "2024-01-28", form="10-K", fp="FY", filed="2024-02-20"),
    ]
    oi = [
        row(40.0, *FY2026, form="10-K", fp="FY", filed="2026-02-20"),
        row(16.0, "2025-01-27", "2025-07-27", form="10-Q", fp="Q2", filed="2025-08-20"),
        row(45.0, "2026-01-26", "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
        row(27.0, "2026-04-27", "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
    ]
    eq = [
        row(300.0, None, "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
        row(100.0, None, "2025-07-27", form="10-Q", fp="Q2", filed="2025-08-20"),
        row(250.0, None, FY2026[1], form="10-K", fp="FY", filed="2026-02-20"),
    ]
    ni = [
        row(30.0, *FY2026, form="10-K", fp="FY", filed="2026-02-20"),
        row(10.0, "2025-01-27", "2025-07-27", form="10-Q", fp="Q2", filed="2025-08-20"),
        row(40.0, "2026-01-26", "2026-07-26", form="10-Q", fp="Q2", filed="2026-08-20"),
    ]
    g = {"Revenues": {"units": {"USD": rev}}, "OperatingIncomeLoss": {"units": {"USD": oi}},
         "StockholdersEquity": {"units": {"USD": eq}}, "NetIncomeLoss": {"units": {"USD": ni}}}
    return extract_facts({"cik": 1, "entityName": "T", "facts": {"us-gaap": g}}, ticker="T")


def by(ms, metric, kind):
    return next(m for m in ms if m.metric == metric and m.period_type == kind)


def test_ttm_is_annual_plus_ytd_minus_prior_ytd():
    facts = build()
    assert latest_end(facts) == "2026-07-26"
    ms = compute_recent_metrics(facts)
    om = by(ms, "operating_margin", "TTM")
    # 매출 TTM = 200 + 150 − 80 = 270, 영업이익 TTM = 40 + 45 − 16 = 69
    assert om.value == pytest.approx(69 / 270)
    assert om.period_label == "TTM ~2026-07-26"
    assert om.components["revenue"] == [[1, "T:revenue:2026-01-25"], [1, "T:revenue:2026-01-26~2026-07-26"],
                                        [-1, "T:revenue:2025-01-27~2025-07-27"]]
    # 1년 전 TTM = 100 + 80 − 30 = 150 → 성장률 270/150 − 1
    assert by(ms, "revenue_growth", "TTM").value == pytest.approx(270 / 150 - 1)
    # ROE = 순이익 TTM (30 + 40 − 10 = 60) / 평균자본 (300 + 100) / 2
    assert by(ms, "roe", "TTM").value == pytest.approx(60 / 200)
    assert recent_scale(facts)["value"] == pytest.approx(270)


def test_latest_quarter_yoy_and_margins():
    ms = compute_recent_metrics(build())
    q = by(ms, "revenue_growth", "Q")
    assert q.value == pytest.approx(90 / 45 - 1) and q.period_label == "FY2027 Q2"
    assert by(ms, "operating_margin", "Q").value == pytest.approx(27 / 90)


def test_annual_metrics_ignore_interim_facts():
    ms = compute_metrics(build())
    assert all(m.period_type == "FY" for m in ms)
    fy26 = next(m for m in ms if m.metric == "operating_margin" and m.fiscal_year == 2026)
    assert fy26.value == pytest.approx(40 / 200)


def test_without_interim_falls_back_to_annual():
    facts = [f for f in build() if f.annual]
    ms = compute_recent_metrics(facts)
    om = by(ms, "operating_margin", "TTM")
    assert om.value == pytest.approx(40 / 200)
    assert om.period_label.startswith("FY2026 연간")
