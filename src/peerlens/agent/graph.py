"""PeerLens Agent (LangGraph).

    plan → peers → financials → ensure_index → research → write → verify ─┬→ finalize
                                                              ↑            │ (실패 & 재시도 < 2)
                                                              └── revise ←─┘

모든 단계는 emit()으로 Trace 이벤트를 남긴다 (웹 화면에 실시간 표시 + 실행 기록 저장).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from peerlens import service
from peerlens.agent.llm import LLM, LLMCall
from peerlens.agent.peers import PeerFinder
from peerlens.agent.refs import PLACEHOLDER, RefTable, build_metric_refs, render_text, segments, stray_numbers
from peerlens.agent.schemas import Judgements, Memo, Plan, PeerSelection, Revision
from peerlens.config import PROJECT_ROOT
from peerlens.metrics.calc import METRICS

MAX_REVISIONS = 2
MIN_PEERS, MAX_PEERS = 3, 6

NODE_TITLES = {
    "plan": "계획 수립",
    "peers": "Peer 선정",
    "financials": "재무 수치 수집·계산",
    "ensure_index": "공시 원문 색인 확인",
    "research": "공시 근거 검색",
    "write": "메모 작성",
    "verify": "검증",
    "revise": "재검색·재작성",
    "finalize": "완료",
}

Emit = Callable[[dict[str, Any]], None]


class State(TypedDict, total=False):
    request: str
    plan: dict[str, Any]
    target: str
    peers: list[str]
    peer_report: list[dict[str, Any]]
    comparison: dict[str, Any]
    memo: dict[str, Any]  # {"title", "blocks": [{"heading", "sentences": [{id, kind, text, evidence_ids}]}]}
    checks: dict[str, dict[str, Any]]  # sentence_id → 검증 결과
    attempt: int
    notes: list[str]


# ---- 프롬프트 -------------------------------------------------------------------

PLANNER_SYSTEM = """너는 국부펀드 해외주식 리서치 Agent의 Planner다. 사용자 요청을 해석해 작업 계획을 JSON으로 만든다.
- 사용할 수 있는 도구: find_peers(업종코드+사업설명 유사도), get_financials·calc_metrics(SEC XBRL 재무 지표), search_filings(10-K/20-F 원문 검색: business, risk_factors, mdna, market_risk 섹션)
- 지표: revenue_growth(매출 성장률), gross_margin, operating_margin, net_margin, roe, rnd_intensity(R&D 비중), fcf_margin
- 요청에 Peer가 명시되지 않았으면 peers는 빈 목록 (도구로 자동 선정)
- evidence_questions는 메모에 필요한 공시 근거 질문 3~4개. 요청이 강조한 관심사(예: 중국 리스크)를 반드시 포함
- keywords_en은 영어 공시 원문에 실제로 나올 법한 단어로"""

PEER_SYSTEM = """너는 Peer 선정 담당이다. 도구가 계산한 후보 점수표(사업설명 유사도·업종코드·매출 규모)와 선정 관점을 보고,
대상 기업과 사업이 실제로 경쟁하거나 비교 가치가 있는 기업을 고른다. 4~6개를 포함(include=true)하고, 모든 후보에 한 문장 이유를 쓴다.
점수가 높아도 사업이 다르면 제외하고, 점수가 조금 낮아도 핵심 경쟁사면 포함할 수 있다. 이유에 숫자는 쓰지 않는다."""

WRITER_SYSTEM = """너는 국부펀드 해외주식 리서치팀의 투자 검토 메모 작성 담당이다. 주어진 '수치 참조 목록'과 '공시 근거 목록'만 사용해 한국어 메모를 쓴다.
규칙:
1. 숫자(%, 금액, 배수, 순위, 개수)를 직접 쓰지 않는다. 수치는 반드시 [[참조ID]] 형태로만 쓴다. 목록에 없는 수치는 언급하지 않는다. 연도 표기(FY2025, CY2025)와 양식명(10-K)은 써도 된다.
2. 문장 종류
   - quant: 수치 비교. 반드시 [[ID]]를 포함.
   - qual: 공시 내용 서술. evidence_ids에 근거 E번호 필수. 근거 문단에 실제로 있는 내용만, 과장·추측 금지.
   - view: 앞의 사실에서 도출한 검토 의견. 새로운 사실이나 수치를 주장하지 않는다.
3. 근거 문단은 영어다. 의미를 정확히 한국어로 옮긴다. 회사명은 티커와 함께 쓴다 (예: 엔비디아(NVDA)).
4. 매수·매도 권고, 목표가는 쓰지 않는다. 판단은 '검토 포인트'로 표현한다.
5. 한 문장에는 한 가지 주장만. 간결하게.
6. 요청에 나온 관심사 표현(예: '지정학적 리스크', '공급망')은 메모에서도 그 표현 그대로 쓴다.
구성: title / summary(핵심 요약 3문장) / sections 4개:
 ① "Peer 구성" — 어떤 기업을 어떤 관점에서 비교했는지 (view 1~2문장, 숫자 없이)
 ② "재무 비교" — 성장성·수익성·효율성 (quant 위주 4~6문장, 대상 기업 vs Peer 중앙값 대비를 중심으로)
 ③ "공시로 본 사업·리스크" — (qual 위주 5~7문장, 요청이 강조한 관심사 포함). 대상 기업만 쓰지 말고
    Peer 근거도 최소 2개 기업을 인용해 대상 기업과 비교·대조한다 (예: 같은 리스크를 Peer는 어떻게 공시했는지)
 ④ "검토 포인트" — 추가 확인이 필요한 사항 (view 2~3문장)"""

JUDGE_SYSTEM = """너는 투자 메모 검증 담당이다. 각 한국어 문장이 인용한 영어 공시 근거 문단에 의해 뒷받침되는지 판정한다.
- supported: 근거 문단이 문장의 주장 전부를 직접 뒷받침 (자연스러운 번역·요약 차이는 허용)
- partial: 일부만 뒷받침되거나, 근거보다 강하게 단정함
- unsupported: 근거에 없거나 모순
판정 이유는 한국어 한 문장."""

REVISER_SYSTEM = WRITER_SYSTEM + """
지금은 검증에 실패한 문장만 고친다. 각 실패 문장에 대해 action을 정한다:
- replace: 지적된 문제를 고친 새 문장 (새로 찾은 근거를 써도 됨)
- drop: 근거로 뒷받침할 수 없으면 문장을 뺀다
실패하지 않은 문장은 건드리지 않는다."""


# ---- 실행 컨텍스트 ---------------------------------------------------------------

class Ctx:
    def __init__(self, emit: Emit):
        self._emit = emit
        self.t0 = time.perf_counter()
        self.seq = 0
        self.llm = LLM()
        self.refs = RefTable()
        self.events: list[dict[str, Any]] = []

    def emit(self, type_: str, node: str, title: str, detail: str = "", data: Any = None) -> None:
        self.seq += 1
        ev = {"seq": self.seq, "t_ms": round((time.perf_counter() - self.t0) * 1000), "type": type_, "node": node,
              "title": title, "detail": detail, "data": data}
        self.events.append(ev)
        self._emit(ev)

    def llm_event(self, node: str, call: LLMCall, what: str) -> None:
        self.emit("llm", node, f"LLM · {what}", f"{call.model} · 입력 {call.input_tokens:,} / 출력 {call.output_tokens:,} 토큰 · {call.ms / 1000:.1f}초",
                  asdict(call))


def _resolve(text: str) -> str:
    t = text.strip().upper()
    try:
        service.client().resolve_ticker(t)
        return t
    except KeyError:
        hits = service.search_companies(text)
        if not hits:
            raise LookupError(f"'{text}'에 해당하는 상장사를 찾지 못했습니다")
        return hits[0]["ticker"]


def _node(name: str):
    """노드 시작·끝 이벤트를 자동으로 남기는 데코레이터."""
    def deco(fn: Callable[[State, Ctx], State]) -> Callable[[State, Ctx], State]:
        def wrapped(state: State, ctx: Ctx) -> State:
            t = time.perf_counter()
            ctx.emit("node_start", name, NODE_TITLES[name])
            out = fn(state, ctx)
            ctx.emit("node_end", name, NODE_TITLES[name], f"{(time.perf_counter() - t):.1f}초")
            return out
        return wrapped
    return deco


# ---- 노드 ----------------------------------------------------------------------

@_node("plan")
def plan_node(state: State, ctx: Ctx) -> State:
    indexed = sorted({f["ticker"] for f in service.indexed_filings()})
    user = f"오늘: {date.today()}\n원문 색인된 기업: {', '.join(indexed)}\n요청: {state['request']}"
    plan, call = ctx.llm.parse(Plan, system=PLANNER_SYSTEM, user=user, name="planner", fast=True)
    ctx.llm_event("plan", call, "요청 해석·작업 계획")
    target = _resolve(plan.target)
    peers = [_resolve(p) for p in plan.peers]
    plan.years = min(max(plan.years or 3, 2), 5)
    ctx.emit("result", "plan", f"대상 {target}", plan.memo_angle, plan.model_dump())
    return {"plan": plan.model_dump(), "target": target, "peers": [p for p in peers if p != target], "notes": []}


@_node("peers")
def peers_node(state: State, ctx: Ctx) -> State:
    target, plan = state["target"], state["plan"]
    if state.get("peers"):
        report = [{"ticker": p, "include": True, "reason": "사용자 지정", "score": None} for p in state["peers"]]
        ctx.emit("result", "peers", "사용자 지정 Peer 사용", ", ".join(state["peers"]), report)
        return {"peer_report": report}

    finder = PeerFinder(service.client(), service.filing_index().embedder)
    ctx.emit("tool", "peers", "find_peers", f"{target} · 후보군에서 업종코드+사업설명 유사도+매출 규모로 상위 12개")
    cands = finder.find(target, k=12)
    ctx.emit("tool_result", "peers", f"후보 {len(cands)}개", ", ".join(c.ticker for c in cands), [c.to_dict() for c in cands])

    table = "\n".join(f"- {c.ticker} ({c.name}): 점수 {c.score:.2f} | {c.reason}" for c in cands)
    tp = finder.profile(target)
    user = (f"대상: {target} ({tp.name if tp else ''})\n대상 사업 요약: {tp.business_excerpt if tp else ''}\n"
            f"선정 관점: {plan['peer_criteria']}\n요청: {state['request']}\n후보:\n{table}")
    sel, call = ctx.llm.parse(PeerSelection, system=PEER_SYSTEM, user=user, name="peer_selector", fast=True)
    ctx.llm_event("peers", call, "Peer 포함·제외 판단")

    by_t = {c.ticker: c for c in cands}
    decided = {d.ticker.upper(): d for d in sel.decisions if d.ticker.upper() in by_t}
    chosen = [t for t, d in decided.items() if d.include][:MAX_PEERS]
    for c in cands:  # 너무 적게 고르면 점수 순으로 보충
        if len(chosen) >= MIN_PEERS:
            break
        if c.ticker not in chosen:
            chosen.append(c.ticker)
    report = [
        {"ticker": c.ticker, "name": c.name, "include": c.ticker in chosen, "score": c.score, "similarity": c.similarity,
         "sic": c.sic, "sic_match": c.sic_match, "size_ratio": c.size_ratio, "tool_reason": c.reason,
         "reason": decided[c.ticker].reason if c.ticker in decided else "점수 순 보충"}
        for c in cands
    ]
    ctx.emit("result", "peers", f"Peer {len(chosen)}개 선정", ", ".join(chosen), report)
    return {"peers": chosen, "peer_report": report}


@_node("financials")
def financials_node(state: State, ctx: Ctx) -> State:
    ctx.emit("tool", "financials", "get_financials · calc_metrics",
             f"{state['target']} + {', '.join(state['peers'])} · {state['plan']['years']}년 · 달력연도 정렬")
    comp = service.compare(state["target"], state["peers"], years=state["plan"]["years"], align="calendar")
    ctx.refs.metrics = build_metric_refs(comp)
    notes = list(state.get("notes", []))
    if comp["missing_latest"]:
        notes.append(f"{', '.join(comp['missing_latest'])}: {comp['year_label']}{comp['latest_year']} 연간 XBRL 미공시 → 해당 칸 비움")
    flagged = sum(1 for c in comp["cells"] if c["flags"])
    ctx.emit("tool_result", "financials", f"지표 {len(comp['cells'])}개 계산",
             f"{comp['year_label']}{comp['years'][0]}~{comp['latest_year']} · 수치 참조 {len(ctx.refs.metrics)}개 · 품질 플래그 {flagged}개",
             {"years": comp["years"], "missing_latest": comp["missing_latest"]})
    return {"comparison": comp, "notes": notes}


@_node("ensure_index")
def ensure_index_node(state: State, ctx: Ctx) -> State:
    from peerlens.retrieval.pipeline import index_tickers

    tickers = [state["target"], *state["peers"]]
    indexed = {f["ticker"] for f in service.indexed_filings()}
    missing = [t for t in tickers if t not in indexed]
    notes = list(state.get("notes", []))
    if not missing:
        ctx.emit("result", "ensure_index", "모두 색인됨", ", ".join(tickers))
        return {}
    ctx.emit("tool", "ensure_index", "index_filings", f"색인 없는 기업 자동 색인: {', '.join(missing)}")
    idx = service.filing_index()
    with service._index_lock:
        reports = index_tickers(idx, service.client(), missing)
    for r in reports:
        if r.error:
            notes.append(f"{r.ticker}: 공시 원문 색인 실패 ({r.error}) → 근거 검색에서 제외")
    ctx.emit("tool_result", "ensure_index", "색인 완료",
             " · ".join(f"{r.ticker} {'실패' if r.error else f'{r.added}청크'}" for r in reports),
             [{"ticker": r.ticker, "added": r.added, "error": r.error} for r in reports])
    return {"notes": notes}


def _register(ctx: Ctx, node: str, question: str, res: dict[str, Any]) -> list[str]:
    added = []
    for h in res["results"]:
        ref = ctx.refs.add_evidence(question, h)
        if ref:
            added.append(ref.ref_id)
    ctx.emit("tool_result", node, f"근거 {len(res['results'])}건 (신규 {len(added)})",
             " · ".join(f"{h['ticker']} {h['section_label']} {h['score']:.2f}" for h in res["results"]),
             {"question": question, "added": added, "elapsed_ms": res["elapsed_ms"]})
    return added


def _search(ctx: Ctx, node: str, question: str, keywords: str | None, tickers: list[str], sections: list[str], k: int) -> list[str]:
    res = service.search_evidence(question, tickers=tickers, sections=sections or None, k=k, keywords=keywords)
    return _register(ctx, node, question, res)


@_node("research")
def research_node(state: State, ctx: Ctx) -> State:
    target, peers = state["target"], state["peers"]
    for q in state["plan"]["evidence_questions"]:
        # 비교 질문은 기업별로 나눠 찾는다 — 한 번에 찾으면 리랭커가 한 회사 문단만 고르는 경향이 있다
        k = {target: 3} if q["target_only"] else {target: 2, **{p: 1 for p in peers}}
        scope = ", ".join(f"{t} {n}건" for t, n in k.items())
        ctx.emit("tool", "research", "search_filings", f"{q['question_ko']}  (키워드: {q['keywords_en']} · {scope})")
        res = service.search_evidence_per_ticker(q["question_ko"], k, sections=q["sections"] or None, keywords=q["keywords_en"])
        _register(ctx, "research", q["question_ko"], res)
    return {}


def _evidence_block(ctx: Ctx, limit: int = 1300) -> str:
    lines = []
    for e in ctx.refs.evidence.values():
        h = e.hit
        head = f"{e.ref_id} [{h['ticker']} {h['form']} {h['section_label']}{' · ' + h['subheading'][:80] if h['subheading'] else ''}]"
        lines.append(f"{head}\n{h['text'][:limit]}")
    return "\n\n".join(lines)


def _metric_block(ctx: Ctx) -> str:
    return "\n".join(f"[[{r.ref_id}]] = {r.display} — {r.label}" for r in ctx.refs.metrics.values() if r.value is not None)


def _with_ids(memo: Memo) -> dict[str, Any]:
    n = 0
    blocks = []
    for heading, sents in [("핵심 요약", memo.summary), *[(s.heading, s.sentences) for s in memo.sections]]:
        out = []
        for s in sents:
            n += 1
            out.append({"id": f"s{n}", **s.model_dump()})
        blocks.append({"heading": heading, "sentences": out})
    return {"title": memo.title, "blocks": blocks}


@_node("write")
def write_node(state: State, ctx: Ctx) -> State:
    plan = state["plan"]
    peers_txt = "\n".join(f"- {p['ticker']}: {p['reason']}" for p in state["peer_report"] if p["include"])
    labels = ", ".join(METRICS[m].label_ko for m in plan["focus_metrics"] if m in METRICS)
    user = (f"요청: {state['request']}\n관점: {plan['memo_angle']}\n강조 지표: {labels}\n"
            f"대상: {state['target']}\nPeer와 선정 이유:\n{peers_txt}\n"
            f"데이터 유의사항: {'; '.join(state.get('notes', [])) or '없음'}\n\n"
            f"## 수치 참조 목록 (이 ID만 [[ ]]로 사용)\n{_metric_block(ctx)}\n\n## 공시 근거 목록\n{_evidence_block(ctx)}")
    memo, call = ctx.llm.parse(Memo, system=WRITER_SYSTEM, user=user, name="writer")
    ctx.llm_event("write", call, "메모 초안 작성")
    m = _with_ids(memo)
    n = sum(len(b["sentences"]) for b in m["blocks"])
    ctx.emit("result", "write", f"초안 {n}문장", memo.title)
    return {"memo": m, "attempt": state.get("attempt", 0)}


def _sentences(memo: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for b in memo["blocks"] for s in b["sentences"]]


@_node("verify")
def verify_node(state: State, ctx: Ctx) -> State:
    checks: dict[str, dict[str, Any]] = {}
    to_judge = []
    for s in _sentences(state["memo"]):
        problems = []
        if stray := stray_numbers(s["text"]):
            problems.append(f"참조 없이 숫자를 직접 씀: {', '.join(stray[:3])}")
        unknown = [m for m in PLACEHOLDER.findall(s["text"]) if m not in ctx.refs.metrics]
        if unknown:
            problems.append(f"존재하지 않는 수치 참조: {', '.join(unknown)}")
        bad_e = [e for e in s["evidence_ids"] if e not in ctx.refs.evidence]
        if bad_e:
            problems.append(f"존재하지 않는 근거: {', '.join(bad_e)}")
        if s["kind"] == "quant" and not PLACEHOLDER.search(s["text"]):
            problems.append("수치 문장인데 수치 참조가 없음")
        if s["kind"] == "qual" and not s["evidence_ids"]:
            problems.append("공시 서술 문장인데 근거가 없음")
        checks[s["id"]] = {"status": "fail" if problems else "pass", "problems": problems, "verdict": None, "reason": None}
        if not problems and s["kind"] == "qual":
            to_judge.append(s)
    n_det_fail = sum(1 for c in checks.values() if c["status"] == "fail")
    ctx.emit("result", "verify", "규칙 검사", f"수치·참조·근거 규칙 위반 {n_det_fail}문장 / 전체 {len(checks)}문장")

    if to_judge:
        parts = []
        for s in to_judge:
            ev = "\n".join(f"[{e}] {ctx.refs.evidence[e].hit['text'][:2200]}" for e in s["evidence_ids"])
            parts.append(f"### {s['id']}\n문장: {render_text(s['text'], ctx.refs)[0]}\n근거:\n{ev}")
        judged, call = ctx.llm.parse(Judgements, system=JUDGE_SYSTEM, user="\n\n".join(parts), name="verifier", fast=True)
        ctx.llm_event("verify", call, f"문장-근거 일치 판정 {len(to_judge)}문장")
        for j in judged.items:
            if j.sentence_id in checks:
                c = checks[j.sentence_id]
                c["verdict"], c["reason"] = j.verdict, j.reason
                if j.verdict != "supported":
                    c["status"] = "fail"
                    c["problems"].append(f"근거 판정 {j.verdict}: {j.reason}")
    fails = [sid for sid, c in checks.items() if c["status"] == "fail"]
    ctx.emit("result", "verify", "검증 통과" if not fails else f"실패 {len(fails)}문장",
             ", ".join(fails), {"checks": checks})
    return {"checks": checks}


def route_after_verify(state: State) -> str:
    fails = [c for c in state["checks"].values() if c["status"] == "fail"]
    return "revise" if fails and state.get("attempt", 0) < MAX_REVISIONS else "finalize"


@_node("revise")
def revise_node(state: State, ctx: Ctx) -> State:
    attempt = state.get("attempt", 0) + 1
    ctx.emit("result", "revise", f"재작성 {attempt}/{MAX_REVISIONS}회차")
    tickers = state["comparison"]["tickers"]
    failed = [s for s in _sentences(state["memo"]) if state["checks"][s["id"]]["status"] == "fail"]

    # 근거 문제인 문장은 그 문장을 질의로 다시 검색 (해당 항목만 재검색)
    for s in failed:
        c = state["checks"][s["id"]]
        if s["kind"] == "qual" and (c["verdict"] in ("partial", "unsupported") or not s["evidence_ids"]):
            mentioned = [t for t in tickers if t in s["text"]] or [state["target"]]
            q = render_text(s["text"], ctx.refs)[0]
            ctx.emit("tool", "revise", "search_filings (재검색)", f"{s['id']}: {q[:80]}… ({', '.join(mentioned)})")
            _search(ctx, "revise", q, None, mentioned, [], k=3)

    items = "\n".join(
        f"- {s['id']} ({s['kind']}): {s['text']} | 근거 {s['evidence_ids']} | 문제: {' / '.join(state['checks'][s['id']]['problems'])}"
        for s in failed
    )
    user = (f"## 실패한 문장\n{items}\n\n## 수치 참조 목록\n{_metric_block(ctx)}\n\n## 공시 근거 목록\n{_evidence_block(ctx)}")
    rev, call = ctx.llm.parse(Revision, system=REVISER_SYSTEM, user=user, name="reviser")
    ctx.llm_event("revise", call, f"실패 {len(failed)}문장 재작성")

    patches = {p.sentence_id: p for p in rev.patches}
    memo = json.loads(json.dumps(state["memo"]))
    dropped = replaced = 0
    for b in memo["blocks"]:
        kept = []
        for s in b["sentences"]:
            p = patches.get(s["id"])
            if p is None or state["checks"][s["id"]]["status"] != "fail":
                kept.append(s)
            elif p.action == "drop":
                dropped += 1
            else:
                replaced += 1
                kept.append({**s, "kind": p.kind, "text": p.text, "evidence_ids": p.evidence_ids, "revised": attempt})
        b["sentences"] = kept
    ctx.emit("result", "revise", f"수정 {replaced} · 삭제 {dropped}")
    return {"memo": memo, "attempt": attempt}


@_node("finalize")
def finalize_node(state: State, ctx: Ctx) -> State:
    return {}


# ---- 그래프 -----------------------------------------------------------------------

def build_graph(ctx: Ctx):
    g = StateGraph(State)
    for name, fn in [("plan", plan_node), ("peers", peers_node), ("financials", financials_node),
                     ("ensure_index", ensure_index_node), ("research", research_node), ("write", write_node),
                     ("verify", verify_node), ("revise", revise_node), ("finalize", finalize_node)]:
        g.add_node(name, lambda s, fn=fn: fn(s, ctx))
    g.add_edge(START, "plan")
    g.add_edge("plan", "peers")
    g.add_edge("peers", "financials")
    g.add_edge("financials", "ensure_index")
    g.add_edge("ensure_index", "research")
    g.add_edge("research", "write")
    g.add_edge("write", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"revise": "revise", "finalize": "finalize"})
    g.add_edge("revise", "verify")
    g.add_edge("finalize", END)
    return g.compile()


def _render(state: State, ctx: Ctx) -> dict[str, Any]:
    checks = state.get("checks", {})
    blocks = []
    for b in state["memo"]["blocks"]:
        sents = []
        for s in b["sentences"]:
            c = checks.get(s["id"], {})
            sents.append({**s, "segments": segments(s["text"], ctx.refs), "status": c.get("status", "pass"),
                          "problems": c.get("problems", []), "verdict": c.get("verdict"), "judge_reason": c.get("reason")})
        blocks.append({"heading": b["heading"], "sentences": sents})
    all_s = [s for b in blocks for s in b["sentences"]]
    used_e = {e for s in all_s for e in s["evidence_ids"]}
    qual = [s for s in all_s if s["kind"] == "qual"]
    quant = [s for s in all_s if s["kind"] == "quant"]
    return {
        "title": state["memo"]["title"],
        "blocks": blocks,
        "evidence": [{"ref_id": e.ref_id, "question": e.question, **e.hit} for e in ctx.refs.evidence.values() if e.ref_id in used_e],
        "stats": {
            "sentences": len(all_s),
            "quant": len(quant),
            "quant_pass": sum(1 for s in quant if s["status"] == "pass"),
            "qual": len(qual),
            "qual_supported": sum(1 for s in qual if s["verdict"] == "supported"),
            "warnings": sum(1 for s in all_s if s["status"] == "fail"),
            "revisions": state.get("attempt", 0),
            "metric_refs_used": len({seg["ref_id"] for s in all_s for seg in s["segments"] if seg["type"] == "metric"}),
            "evidence_used": len(used_e),
        },
    }


def run_agent(request: str, emit: Emit | None = None) -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    ctx = Ctx(emit or (lambda e: None))
    ctx.emit("run_start", "run", "Agent 실행 시작", request, {"run_id": run_id})
    result: dict[str, Any] = {"run_id": run_id, "request": request, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        state = build_graph(ctx).invoke({"request": request})
        result.update({
            "status": "ok",
            "target": state["target"],
            "peers": state["peers"],
            "plan": state["plan"],
            "peer_report": state["peer_report"],
            "comparison": state["comparison"],
            "notes": state.get("notes", []),
            "memo": _render(state, ctx),
        })
    except Exception as e:  # 실패도 Trace로 남긴다
        ctx.emit("error", "run", "실행 실패", f"{type(e).__name__}: {e}")
        result.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
    result["elapsed_ms"] = round((time.perf_counter() - ctx.t0) * 1000)
    result["llm_usage"] = ctx.llm.usage()
    result["models"] = {"main": ctx.llm.model, "fast": ctx.llm.fast_model}
    ctx.emit("run_end", "run", "완료" if result["status"] == "ok" else "실패", f"{result['elapsed_ms'] / 1000:.1f}초",
             {"llm_usage": result["llm_usage"]})
    result["trace"] = ctx.events
    out = PROJECT_ROOT / "data" / "runs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result
