"""XBRL 태그 매핑표: 표준 지표 → (taxonomy, tag) 우선순위 목록.

회사·연도마다 같은 항목을 다른 태그로 공시하므로, 기간별로 우선순위가 가장 높은 태그 값을 쓴다.
`exact=False`인 태그는 정의가 조금 다른 대체 태그(예: 비지배지분 포함)이며, 결과에 품질 플래그로 남긴다.

근거 사례 (2026-10 companyfacts 기준):
- AMD·INTC·AVGO 매출은 RevenueFromContractWithCustomerExcludingAssessedTax, NVDA·QCOM은 Revenues
- AVGO 순이익: FY2024까지 NetIncomeLoss, FY2025는 ProfitLoss만 존재
- AVGO·QCOM 자본: 2019년 이후 StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest만 존재
- NVDA·QCOM CAPEX: PaymentsToAcquireProductiveAssets
- QCOM은 GrossProfit 태그가 없어 매출 - 매출원가로 계산 (calc 모듈)
- TSMC는 ifrs-full taxonomy, TWD 단위
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["duration", "instant"]


@dataclass(frozen=True)
class TagRule:
    taxonomy: str
    tag: str
    exact: bool = True  # False → 정의가 다른 대체 태그 (proxy)


@dataclass(frozen=True)
class Concept:
    name: str
    label_ko: str
    kind: Kind
    tags: tuple[TagRule, ...]


def _g(tag: str, exact: bool = True) -> TagRule:
    return TagRule("us-gaap", tag, exact)


def _i(tag: str, exact: bool = True) -> TagRule:
    return TagRule("ifrs-full", tag, exact)


CONCEPTS: dict[str, Concept] = {
    c.name: c
    for c in [
        Concept("revenue", "매출", "duration", (
            _g("RevenueFromContractWithCustomerExcludingAssessedTax"),
            _g("Revenues"),
            _g("SalesRevenueNet"),
            _g("RevenueFromContractWithCustomerIncludingAssessedTax"),
            _i("Revenue"),
            _i("RevenueFromContractsWithCustomers"),
        )),
        Concept("cost_of_revenue", "매출원가", "duration", (
            _g("CostOfRevenue"),
            _g("CostOfGoodsAndServicesSold"),
            _g("CostOfGoodsSold"),
            _i("CostOfSales"),
        )),
        Concept("gross_profit", "매출총이익", "duration", (
            _g("GrossProfit"),
            _i("GrossProfit"),
        )),
        Concept("operating_income", "영업이익", "duration", (
            _g("OperatingIncomeLoss"),
            _i("ProfitLossFromOperatingActivities"),
        )),
        Concept("net_income", "순이익(지배주주)", "duration", (
            _g("NetIncomeLoss"),
            _i("ProfitLossAttributableToOwnersOfParent"),
            _g("NetIncomeLossAvailableToCommonStockholdersBasic", exact=False),
            _g("ProfitLoss", exact=False),
            _i("ProfitLoss", exact=False),
        )),
        Concept("rnd_expense", "연구개발비", "duration", (
            _g("ResearchAndDevelopmentExpense"),
            _g("ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
            _i("ResearchAndDevelopmentExpense"),
        )),
        Concept("operating_cash_flow", "영업활동현금흐름", "duration", (
            _g("NetCashProvidedByUsedInOperatingActivities"),
            _i("CashFlowsFromUsedInOperatingActivities"),
        )),
        Concept("capex", "설비투자(CAPEX)", "duration", (
            _g("PaymentsToAcquirePropertyPlantAndEquipment"),
            _g("PaymentsToAcquireProductiveAssets"),
            _i("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
        )),
        Concept("total_assets", "총자산", "instant", (
            _g("Assets"),
            _i("Assets"),
        )),
        Concept("equity", "자본(지배주주)", "instant", (
            _g("StockholdersEquity"),
            _i("EquityAttributableToOwnersOfParent"),
            _g("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", exact=False),
            _i("Equity", exact=False),
        )),
    ]
}

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
