export const pct = (v: number | null | undefined, digits = 1) =>
  v == null || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(digits)}%`;

/** 비율 차이를 %p로. 부호를 텍스트로 드러낸다 (색만으로 구분하지 않음). */
export const ppDiff = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return "–";
  const p = v * 100;
  const sign = p > 0.05 ? "+" : p < -0.05 ? "−" : "±";
  return `${sign}${Math.abs(p).toFixed(1)}%p`;
};

const CURRENCY_SYMBOL: Record<string, string> = { USD: "$", EUR: "€", TWD: "NT$", JPY: "¥", KRW: "₩", GBP: "£" };

/** 원값을 백만 단위로 (공시 원문 표기와 동일한 단위). */
export const money = (v: number, unit: string) => {
  const sym = CURRENCY_SYMBOL[unit];
  const m = (v / 1e6).toLocaleString("ko-KR", { maximumFractionDigits: 0 });
  return sym ? `${sym}${m}M` : `${m}M ${unit}`;
};

export type FlagInfo = { kind: "proxy" | "derived" | "restated" | "missing" | "note"; text: string };

const CONCEPT_KO: Record<string, string> = {
  revenue: "매출",
  cost_of_revenue: "매출원가",
  gross_profit: "매출총이익",
  operating_income: "영업이익",
  net_income: "순이익",
  equity: "자본",
  rnd_expense: "연구개발비",
  operating_cash_flow: "영업현금흐름",
  capex: "설비투자",
  total_assets: "총자산",
};

/** calc 모듈의 품질 플래그를 사람이 읽는 설명으로. */
export function describeFlag(flag: string): FlagInfo {
  const [head, rest = ""] = flag.split(/:(.*)/s);
  switch (head) {
    case "proxy_tag": {
      const [concept, tag] = rest.split("=");
      return { kind: "proxy", text: `${CONCEPT_KO[concept] ?? concept}: 표준 태그가 없어 대체 태그 ${tag} 사용 (비지배지분 포함 등 정의가 일부 다름)` };
    }
    case "derived":
      return { kind: "derived", text: "매출총이익 태그가 없어 매출 − 매출원가로 계산" };
    case "restated":
      return { kind: "restated", text: `이후 공시에서 수정된 값 — 최신 공시 기준 사용 (${rest})` };
    case "ending_equity_only":
      return { kind: "note", text: "전년 말 자본이 없어 기말 자본으로 계산" };
    case "non_positive_equity":
      return { kind: "missing", text: "자본이 0 이하라 ROE를 계산하지 않음" };
    case "missing":
      return { kind: "missing", text: rest === "prior_year_revenue" ? "전년 매출 데이터 없음" : `데이터 없음 (${rest})` };
    case "missing_input":
      return { kind: "missing", text: "계산에 필요한 공시 값이 없음" };
    default:
      return { kind: "note", text: flag };
  }
}

export const flagLabel: Record<FlagInfo["kind"], string> = {
  proxy: "대체 태그",
  derived: "계산값",
  restated: "재작성",
  missing: "데이터 없음",
  note: "참고",
};

export const conceptKo = (c: string) => CONCEPT_KO[c] ?? c;
