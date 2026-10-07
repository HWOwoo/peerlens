"""Agent 노드 간 입출력 형식. LLM 출력은 모두 이 스키마로 강제한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MetricName = Literal[
    "revenue_growth", "gross_margin", "operating_margin", "net_margin", "roe", "rnd_intensity", "fcf_margin"
]
SectionName = Literal["business", "risk_factors", "mdna", "market_risk"]


# ---- Planner ---------------------------------------------------------------

class EvidenceQuestion(BaseModel):
    question_ko: str = Field(description="공시 원문에서 찾을 질문 (한국어)")
    keywords_en: str = Field(description="영어 원문 키워드 검색용 질의 (5~10 단어)")
    sections: list[SectionName] = Field(description="찾을 섹션. 모르면 빈 목록")
    target_only: bool = Field(description="대상 기업만 찾으면 true, Peer 전체에서 찾으면 false")


class Plan(BaseModel):
    target: str = Field(description="대상 기업의 미국 상장 티커 (예: 퀄컴 → QCOM, TSMC → TSM). 한국어·영어 회사명은 티커로 바꿔 쓴다")
    peers: list[str] = Field(description="요청에 Peer 기업이 이름으로 명시된 경우만 그 티커 목록. '반도체 Peer'처럼 범주만 말했으면 빈 목록 (도구로 자동 선정)")
    peer_criteria: str = Field(description="Peer 선정 시 고려할 관점 (예: AI 가속기 경쟁사 중심)")
    focus_metrics: list[MetricName] = Field(description="메모에서 강조할 지표 2~4개")
    years: int = Field(description="비교 기간(년), 기본 3")
    evidence_questions: list[EvidenceQuestion] = Field(
        description="공시 근거 질문 3~4개. 수치 차이의 '원인'(제품 믹스·가격·수요·비용)을 찾는 질문을 최소 1개 포함")
    memo_angle: str = Field(description="메모의 관점·초점 한 문장")


# ---- Peer 선택 ---------------------------------------------------------------

class PeerDecision(BaseModel):
    ticker: str
    include: bool
    reason: str = Field(description="포함/제외 이유 한 문장 (한국어)")


class PeerSelection(BaseModel):
    decisions: list[PeerDecision] = Field(description="후보 전부에 대한 판단")


# ---- Analyst: 메모를 쓰기 전 분석 설계 -------------------------------------------------

InsightType = Literal["strength", "weakness", "driver", "risk", "watch"]


class Insight(BaseModel):
    type: InsightType = Field(description="strength=강점, weakness=약점, driver=수치를 만든 원인, risk=리스크, watch=확인할 점")
    claim: str = Field(description="분석 주장 한 문장. 숫자는 [[참조ID]]로만")
    metric_refs: list[str] = Field(description="주장을 받치는 수치 참조 ID")
    evidence_ids: list[str] = Field(description="주장을 받치는 공시 근거 E번호 (원인·맥락)")
    so_what: str = Field(description="투자 검토 관점의 시사점 한 문장 (새 숫자 없이)")


class AnalysisPlan(BaseModel):
    thesis: str = Field(description="대상 기업에 대한 핵심 논지 한 문장 (매수·매도 권고 아님)")
    insights: list[Insight] = Field(description="인사이트 5~8개. 수치와 근거를 짝지어 '무엇이 → 왜 → 그래서' 구조")
    peer_contrasts: list[str] = Field(description="Peer와 대비되는 점 2~3개 (Peer 티커 명시, 숫자는 [[참조ID]])")
    watch_items: list[str] = Field(description="다음 공시·데이터에서 확인할 구체적 항목 2~3개 (무엇을, 어디서)")


# ---- Writer ----------------------------------------------------------------

SentenceKind = Literal["quant", "qual", "view"]


class Sentence(BaseModel):
    kind: SentenceKind = Field(description="quant=수치 비교(원인 근거가 있으면 evidence_ids도), qual=공시 근거 서술, view=분석 의견(새 사실 없음)")
    text: str = Field(description="한국어 문장. 숫자는 반드시 [[참조ID]]로만 표기")
    evidence_ids: list[str] = Field(description="문장이 근거로 삼은 E번호 목록 (qual은 필수, quant는 원인을 말할 때)")


class MemoSection(BaseModel):
    heading: str
    sentences: list[Sentence]


class Memo(BaseModel):
    title: str
    summary: list[Sentence] = Field(description="핵심 요약 3문장: 논지 · 핵심 근거 · 핵심 리스크")
    sections: list[MemoSection]


# ---- Verifier --------------------------------------------------------------

Verdict = Literal["supported", "partial", "unsupported"]


class Judgement(BaseModel):
    sentence_id: str
    verdict: Verdict
    reason: str = Field(description="판정 이유 한 문장 (한국어)")


class Judgements(BaseModel):
    items: list[Judgement]


# ---- 재작성 -------------------------------------------------------------------

class SentencePatch(BaseModel):
    sentence_id: str
    action: Literal["replace", "drop"]
    kind: SentenceKind
    text: str = Field(description="replace일 때 새 문장, drop이면 빈 문자열")
    evidence_ids: list[str]


class Revision(BaseModel):
    patches: list[SentencePatch]
