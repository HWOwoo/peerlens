"""PeerLens 평가: 질문 세트로 Agent를 실행하고, 결과를 Agent 내부 검증과 독립적으로 다시 확인한다.

    python eval/run_eval.py              # 전체
    python eval/run_eval.py q01 q03      # 일부
    python eval/run_eval.py --rescore    # 실행 없이 저장된 결과만 다시 채점

측정 항목
- 성공률, 소요 시간, LLM 호출·토큰
- 수치 일치율: 메모의 모든 수치를 SEC 원본 JSON에서 다시 읽고 별도 수식으로 재계산해 표시값과 대조
- LLM 직접 숫자: 자리표시자 밖 숫자 수 (0이어야 함)
- 인용 원문 존재율: 인용 문단이 10-K/20-F 원문에 실제로 있는지
- 근거 일치율: Agent 내부 판정 + 다른 모델(메인 모델)의 독립 재판정
- 요청 반영: 대상 기업, 지정 Peer, 관심 키워드 포함
- Peer 근거 커버리지
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")

from peerlens import service  # noqa: E402
from peerlens.agent import run_agent  # noqa: E402
from peerlens.agent.graph import JUDGE_SYSTEM  # noqa: E402
from peerlens.agent.llm import LLM  # noqa: E402
from peerlens.agent.refs import stray_numbers  # noqa: E402
from peerlens.agent.schemas import Judgements  # noqa: E402

# ---- 수치 독립 재계산 ---------------------------------------------------------------


def _raw_value(fact: dict[str, Any]) -> float | None:
    """캐시된 SEC companyfacts 원본 JSON에서 같은 태그·접수번호·기간의 값을 직접 찾는다."""
    data = service.client().company_facts(fact["cik"]).data
    rows = data["facts"].get(fact["taxonomy"], {}).get(fact["tag"], {}).get("units", {}).get(fact["unit"], [])
    for r in rows:
        if r["accn"] == fact["accn"] and r["end"] == fact["period_end"] and r.get("start") == fact["period_start"]:
            return float(r["val"])
    return None


def recompute(metric_id: str) -> tuple[float | None, list[str]]:
    """calc 모듈을 쓰지 않고 원본 값으로 지표를 다시 계산. (값, 문제 목록)"""
    d = service.metric_detail(metric_id)
    problems: list[str] = []
    cur: dict[str, float] = {}
    prev: dict[str, float] = {}
    for f in d["input_facts"]:
        raw = _raw_value(f)
        if raw is None:
            problems.append(f"원본에 없음: {f['fact_id']}")
            continue
        if raw != f["value"]:
            problems.append(f"원본 불일치: {f['fact_id']} {raw} != {f['value']}")
        (cur if f["period_end"] == d["period_end"] else prev)[f["concept"]] = raw
    m = d["metric"]
    try:
        if m == "revenue_growth":
            v = cur["revenue"] / prev["revenue"] - 1
        elif m == "gross_margin":
            v = (cur["gross_profit"] if "gross_profit" in cur else cur["revenue"] - cur["cost_of_revenue"]) / cur["revenue"]
        elif m == "operating_margin":
            v = cur["operating_income"] / cur["revenue"]
        elif m == "net_margin":
            v = cur["net_income"] / cur["revenue"]
        elif m == "rnd_intensity":
            v = cur["rnd_expense"] / cur["revenue"]
        elif m == "fcf_margin":
            v = (cur["operating_cash_flow"] - cur["capex"]) / cur["revenue"]
        elif m == "roe":
            eq = (cur["equity"] + prev["equity"]) / 2 if "equity" in prev else cur["equity"]
            v = cur["net_income"] / eq
        else:
            return None, [f"알 수 없는 지표 {m}"]
    except (KeyError, ZeroDivisionError) as e:
        return None, problems + [f"재계산 불가: {e}"]
    return v, problems


def _fact_meta(fact_id: str) -> dict[str, Any]:
    ticker = fact_id.split(":", 1)[0]
    return next(f.to_dict() for f in service.company_facts(ticker) if f.fact_id == fact_id)


def _sum_terms(terms: list[list]) -> tuple[float | None, list[str]]:
    """[부호, fact_id] 목록을 SEC 원본 값으로 다시 더한다."""
    total, problems = 0.0, []
    for sign, fid in terms:
        raw = _raw_value(_fact_meta(fid))
        if raw is None:
            problems.append(f"원본에 없음: {fid}")
            return None, problems
        total += sign * raw
    return total, problems


def recompute_recent(metric_id: str) -> tuple[float | None, list[str]]:
    """TTM·분기 지표: 구성 내역(부호·원값 ID)대로 원본 값을 다시 더하고 지표 수식을 독립 적용."""
    ticker = metric_id.split(":", 1)[0]
    m = next(x for x in service.company_metrics(ticker) if x.metric_id == metric_id)
    v: dict[str, float] = {}
    problems: list[str] = []
    for key, terms in (m.components or {}).items():
        val, probs = _sum_terms(terms)
        problems += probs
        if val is None:
            return None, problems
        v[key] = val
    try:
        if m.metric == "revenue_growth":
            out = v["revenue"] / v["revenue_prev"] - 1
        elif m.metric == "gross_margin":
            out = (v["gross_profit"] if "gross_profit" in v else v["revenue"] - v["cost_of_revenue"]) / v["revenue"]
        elif m.metric == "operating_margin":
            out = v["operating_income"] / v["revenue"]
        elif m.metric == "net_margin":
            out = v["net_income"] / v["revenue"]
        elif m.metric == "rnd_intensity":
            out = v["rnd_expense"] / v["revenue"]
        elif m.metric == "fcf_margin":
            out = (v["operating_cash_flow"] - v["capex"]) / v["revenue"]
        elif m.metric == "roe":
            eq = (v["equity"] + v["equity_prev"]) / 2 if "equity_prev" in v else v["equity"]
            out = v["net_income"] / eq
        else:
            return None, [f"알 수 없는 지표 {m.metric}"]
    except (KeyError, ZeroDivisionError) as e:
        return None, problems + [f"재계산 불가: {e}"]
    return out, problems


def _shown(text: str) -> float | None:
    m = re.search(r"([+−-]?)(\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        return None
    return float(m.group(2).replace(",", "")) * (-1 if m.group(1) in ("−", "-") else 1)


def check_numbers(result: dict[str, Any]) -> dict[str, Any]:
    comp = result["comparison"]
    target = comp["target"]
    cell_ids = {(c["ticker"], c["metric"], c["year"]): c["metric_id"] for c in comp["cells"]}
    cell_ids.update({(c["ticker"], c["metric"], c["period_type"]): c["metric_id"] for c in comp.get("recent_cells", [])})
    stale = set(comp.get("stale", []))
    cache: dict[str, tuple[float | None, list[str]]] = {}

    def rc(mid: str) -> tuple[float | None, list[str]]:
        if mid not in cache:
            cache[mid] = recompute_recent(mid) if (":TTM:" in mid or ":Q:" in mid) else recompute(mid)
        return cache[mid]

    def median_of(metric: str, period) -> float | None:
        vals = [rc(cell_ids[(t, metric, period)])[0] for t in comp["tickers"][1:]
                if (t, metric, period) in cell_ids and not (isinstance(period, str) and t in stale)]  # 대상·오래된 데이터 제외
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    total = ok = 0
    errors = []
    for b in result["memo"]["blocks"]:
        for s in b["sentences"]:
            for seg in s["segments"]:
                if seg["type"] != "metric":
                    continue
                total += 1
                kind, metric, period = seg["ref_id"].split(".")
                period = int(period) if period.isdigit() else period
                scale = period == "SCALE"
                if kind == "RANK":  # 최근 12개월 순위: 오래된 데이터 제외, 높을수록 1위
                    vals = {t: rc(cell_ids[(t, metric, "TTM")])[0] for t in comp["tickers"]
                            if (t, metric, "TTM") in cell_ids and t not in stale}
                    vals = {t: v for t, v in vals.items() if v is not None}
                    mine = vals.get(target)
                    rank = None if mine is None else 1 + sum(1 for v in vals.values() if v > mine)
                    shown = _shown(seg["text"])
                    if rank is not None and shown == rank:
                        ok += 1
                    else:
                        errors.append(f"{seg['ref_id']}: 표시 {seg['text']} / 재계산 {rank}위")
                    continue
                if period == "CHG":  # 대상 기업 연간 변화폭
                    y0, y1 = comp["years"][0], comp["years"][-1]
                    a, b = cell_ids.get((kind, metric, y0)), cell_ids.get((kind, metric, y1))
                    va, vb = (rc(a)[0] if a else None), (rc(b)[0] if b else None)
                    expect = None if va is None or vb is None else vb - va
                    shown = _shown(seg["text"])
                    if expect is not None and shown is not None and abs(round(expect * 100, 1) - shown) < 0.051:
                        ok += 1
                    else:
                        errors.append(f"{seg['ref_id']}: 표시 {seg['text']} / 재계산 {expect}")
                    continue
                if scale:
                    sc = comp["recent_periods"][kind]["scale"]
                    expect, probs = _sum_terms(sc["terms"])
                    if probs:
                        errors.append(f"{seg['ref_id']}: {'; '.join(probs)}")
                elif kind == "PEER":
                    expect = median_of(metric, period)
                elif kind == "DIFF":
                    tid = cell_ids.get((target, metric, period))
                    t, med = (rc(tid)[0] if tid else None), median_of(metric, period)
                    expect = None if t is None or med is None else t - med
                else:
                    mid = cell_ids.get((kind, metric, period))
                    expect, probs = rc(mid) if mid else (None, ["비교표에 없는 참조"])
                    if probs:
                        errors.append(f"{seg['ref_id']}: {'; '.join(probs)}")
                shown = _shown(seg["text"])
                if scale:
                    good = expect is not None and shown is not None and abs(round(expect / 1e9, 1) - shown) < 0.051
                else:
                    good = expect is not None and shown is not None and abs(round(expect * 100, 1) - shown) < 0.051
                if good:
                    ok += 1
                else:
                    shown_expect = None if expect is None else (round(expect / 1e9, 2) if scale else round(expect * 100, 2))
                    errors.append(f"{seg['ref_id']}: 표시 {seg['text']} / 재계산 {shown_expect}")
    stray = [n for b in result["memo"]["blocks"] for s in b["sentences"] for n in stray_numbers(s["text"])]
    return {"numbers_total": total, "numbers_ok": ok, "number_errors": errors, "stray_numbers": len(stray), "stray_examples": stray[:5]}


# ---- 인용 원문 존재 확인 ---------------------------------------------------------------

_doc_cache: dict[str, str] = {}


def _doc_text(url: str) -> str:
    if url not in _doc_cache:
        m = re.search(r"/data/(\d+)/(\d+)/([^/#]+)$", url)
        cik, accn_nodash, doc = m.group(1), m.group(2), m.group(3)
        accn = f"{accn_nodash[:10]}-{accn_nodash[10:12]}-{accn_nodash[12:]}"
        html = service.client().get_text(url, f"filings/{cik}/{accn}/{doc}").data
        _doc_cache[url] = re.sub(r"\s+", "", BeautifulSoup(html, "lxml").get_text(" ")).lower()
    return _doc_cache[url]


def check_citations(result: dict[str, Any]) -> dict[str, Any]:
    """인용 문단의 모든 줄(공백 제외 30자 이상)이 공시 원문에 그대로 있는지."""
    found = 0
    missing = []
    for e in result["memo"]["evidence"]:
        doc = _doc_text(e["source_url"]).replace("-", "")
        lines = [re.sub(r"\s+", "", ln).lower().replace("-", "") for ln in e["text"].split("\n")]
        lines = [ln for ln in lines if len(ln) >= 30]
        if lines and all(ln in doc for ln in lines):
            found += 1
        else:
            missing.append(e["ref_id"])
    return {"citations": len(result["memo"]["evidence"]), "citations_found": found, "citations_missing": missing}


# ---- 독립 재판정 -------------------------------------------------------------------


def independent_judge(result: dict[str, Any], llm: LLM) -> dict[str, Any]:
    ev = {e["ref_id"]: e for e in result["memo"]["evidence"]}
    quals = [s for b in result["memo"]["blocks"] for s in b["sentences"] if s["evidence_ids"]]  # 근거가 달린 모든 문장
    if not quals:
        return {"judge_total": 0, "judge_supported": 0, "judge_details": []}
    parts = []
    for s in quals:
        text = "".join(seg["text"] for seg in s["segments"])
        cited = "\n".join(f"[{i}] {ev[i]['text'][:2200]}" for i in s["evidence_ids"] if i in ev)
        parts.append(f"### {s['id']}\n문장: {text}\n근거:\n{cited}")
    judged, call = llm.parse(Judgements, system=JUDGE_SYSTEM, user="\n\n".join(parts), name="independent_judge", fast=False)
    details = [j.model_dump() for j in judged.items]
    return {
        "judge_model": call.model,
        "judge_total": len(quals),
        "judge_supported": sum(1 for j in judged.items if j.verdict == "supported"),
        "judge_partial": sum(1 for j in judged.items if j.verdict == "partial"),
        "judge_details": [d for d in details if d["verdict"] != "supported"],
    }


# ---- 메모 품질 (분석 깊이 등) ----------------------------------------------------------------

from difflib import SequenceMatcher  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

QUALITY_CRITERIA = {
    "insight": "분석 깊이 — 수치 나열이 아니라 해석·원인·시사점을 제시하는가",
    "linkage": "수치-근거 연결 — 정량 결과의 원인을 공시 근거와 연결해 설명하는가",
    "recency": "최신성 — 최근 12개월·최근 분기 기준을 쓰고 기간을 명확히 밝히는가",
    "comparison": "Peer 대비 — 대상 기업만이 아니라 Peer의 수치·공시와 실제로 비교·대조하는가",
    "relevance": "요청 충실도 — 요청한 관심사를 중심으로 구성했는가",
    "concision": "간결성 — 같은 틀의 문장 반복·중복 없이 읽히는가",
    "actionability": "투자 검토 유용성 — 검토 포인트가 구체적이고 다음에 확인할 것을 알려 주는가",
}


class QualityScore(BaseModel):
    criterion: str
    score: int = Field(description="1(매우 부족)~5(전문 애널리스트 수준)")
    reason: str = Field(description="한 문장 근거 (한국어)")


class QualityReport(BaseModel):
    scores: list[QualityScore]
    best: str = Field(description="가장 좋은 점 한 문장")
    worst: str = Field(description="가장 아쉬운 점 한 문장")


QUALITY_SYSTEM = (
    "너는 국부펀드 해외주식 리서치팀장이다. 주니어가 쓴 Peer 비교 투자 검토 메모를 아래 기준별로 1~5점 채점한다. "
    "5점은 바로 투자위원회 자료로 쓸 수 있는 수준, 3점은 사실은 맞지만 해석이 얕은 수준, 1점은 쓸모없는 수준. 후하게 주지 마라.\n"
    + "\n".join(f"- {k}: {v}" for k, v in QUALITY_CRITERIA.items())
)


def _memo_text(result: dict[str, Any]) -> str:
    lines = [result["memo"]["title"]]
    for b in result["memo"]["blocks"]:
        lines.append(f"\n[{b['heading']}]")
        for s in b["sentences"]:
            cite = f" ({', '.join(s['evidence_ids'])})" if s["evidence_ids"] else ""
            lines.append(f"- {''.join(seg['text'] for seg in s['segments'])}{cite}")
    return "\n".join(lines)


def repetition_ratio(result: dict[str, Any]) -> float:
    """수치 문장 중 다른 수치 문장과 골격(숫자 제거)이 80% 이상 같은 문장의 비율."""
    sk = [re.sub(r"[\d.,+−%$B()]+|CY\d{4}|FY\d{4}", "#", "".join(g["text"] for g in s["segments"]))
          for b in result["memo"]["blocks"] for s in b["sentences"] if s["kind"] == "quant"]
    if len(sk) < 2:
        return 0.0
    dup = sum(1 for i, a in enumerate(sk) if any(SequenceMatcher(None, a, b).ratio() >= 0.8 for j, b in enumerate(sk) if i != j))
    return dup / len(sk)


def memo_quality(q: dict[str, Any], result: dict[str, Any], llm: LLM) -> dict[str, Any]:
    user = f"요청: {q['request']}\n\n메모:\n{_memo_text(result)}"
    rep, _ = llm.parse(QualityReport, system=QUALITY_SYSTEM, user=user, name="quality_judge", fast=False)
    scores = {x.criterion: x.score for x in rep.scores if x.criterion in QUALITY_CRITERIA}
    text = _memo_text(result)
    return {
        "quality": scores,
        "quality_avg": round(sum(scores.values()) / len(scores), 2) if scores else None,
        "quality_best": rep.best,
        "quality_worst": rep.worst,
        "repetition": round(repetition_ratio(result), 2),
        "mentions_recent": any(k in text for k in ("최근 12개월", "TTM", "최근 분기", "전년 동기")),
    }


# ---- 요청 반영 ---------------------------------------------------------------------


def check_request(q: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    text = " ".join("".join(seg["text"] for seg in s["segments"]) for b in result["memo"]["blocks"] for s in b["sentences"])
    peers = result.get("peers", [])
    cited_tickers = {e["ticker"] for e in result["memo"]["evidence"]}
    return {
        "target_ok": result.get("target") == q["expect_target"],
        "peers_ok": set(q.get("expect_peers", [])) <= set(peers),
        "mentions_missing": [k for k in q.get("must_mention", []) if k.lower() not in text.lower()],
        "peer_evidence_coverage": (sum(1 for p in peers if p in cited_tickers) / len(peers)) if peers else None,
    }


# ---- 실행 ---------------------------------------------------------------------------


def score(q: dict[str, Any], result: dict[str, Any], llm: LLM | None) -> dict[str, Any]:
    """llm=None이면 LLM 채점(독립 재판정·품질 채점)을 건너뛴다 — 코드 검증(수치·인용·요청 반영)만, 비용 0."""
    row: dict[str, Any] = {"id": q["id"], "request": q["request"], "run_id": result["run_id"], "status": result["status"],
                           "elapsed_s": round(result["elapsed_ms"] / 1000, 1), "llm_calls": result["llm_usage"]["calls"],
                           "tokens_in": result["llm_usage"]["input_tokens"], "tokens_out": result["llm_usage"]["output_tokens"]}
    if result["status"] != "ok":
        row["error"] = result.get("error")
        row["success"] = False
        return row
    st = result["memo"]["stats"]
    row.update({
        "target": result["target"], "peers": result["peers"],
        "sentences": st["sentences"], "revisions": st["revisions"], "warnings": st["warnings"],
        "agent_qual_supported": st["qual_supported"], "agent_qual": st["qual"],
        **check_numbers(result), **check_citations(result), **check_request(q, result),
        **(independent_judge(result, llm) if llm else {}),
        **(memo_quality(q, result, llm) if llm else {"repetition": round(repetition_ratio(result), 2)}),
    })
    row["success"] = st["sentences"] >= 10 and row["target_ok"]
    return row


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.1f}%" if b else "–"


def report(rows: list[dict[str, Any]], path: Path, models: dict[str, str], judge_usage: dict | None = None) -> None:
    ok = [r for r in rows if r.get("success")]
    done = [r for r in rows if r["status"] == "ok"]
    s = lambda k: sum(r.get(k, 0) or 0 for r in done)  # noqa: E731
    times = [r["elapsed_s"] for r in rows if r["status"] == "ok"]
    cover = [r["peer_evidence_coverage"] for r in done if r.get("peer_evidence_coverage") is not None]
    lines = [
        "# PeerLens 평가 보고서",
        "",
        f"- 실행일: {datetime.now():%Y-%m-%d %H:%M} · 질문 {len(rows)}개 (`eval/questions.json`)",
        f"- 모델: 작성 `{models['main']}` / 계획·판정 `{models['fast']}` · 독립 재판정 `{models['main']}`",
        "- 재현: `python eval/run_eval.py` (결과 원본: `eval/results/`)",
        "",
        "## 요약",
        "",
        "| 지표 | 결과 | 측정 방법 |",
        "|---|---|---|",
        f"| 성공률 | **{len(ok)}/{len(rows)}** | 오류 없이 10문장 이상 메모 + 대상 기업 정확 |",
        f"| 평균 소요 시간 | **{statistics.mean(times):.0f}초** (중앙값 {statistics.median(times):.0f}초, 최대 {max(times):.0f}초) | 요청 → 검증 완료 |" if times else "| 평균 소요 시간 | – | |",
        f"| 수치 일치율 | **{_pct(s('numbers_ok'), s('numbers_total'))}** ({s('numbers_ok')}/{s('numbers_total')}) | 메모의 모든 수치를 SEC 원본 JSON에서 다시 읽어 별도 수식으로 재계산·대조 |",
        f"| LLM이 직접 쓴 숫자 | **{s('stray_numbers')}개** | 자리표시자 밖 숫자 |",
        f"| 인용 원문 존재율 | **{_pct(s('citations_found'), s('citations'))}** ({s('citations_found')}/{s('citations')}) | 인용 문단의 모든 줄이 공시 원문에 그대로 있는지 |",
        f"| 근거 일치율 (Agent 판정) | **{_pct(s('agent_qual_supported'), s('agent_qual'))}** ({s('agent_qual_supported')}/{s('agent_qual')}) | 최종 메모의 공시 서술 문장 |",
        f"| 근거 일치율 (독립 재판정) | **{_pct(s('judge_supported'), s('judge_total'))}** ({s('judge_supported')}/{s('judge_total')}) | 다른 모델이 문장-근거를 다시 판정 |",
        f"| 대상 기업 정확도 | {sum(1 for r in done if r['target_ok'])}/{len(done)} | 요청의 기업명 → 티커 |",
        f"| 지정 Peer 반영 | {sum(1 for r in done if r['peers_ok'])}/{len(done)} | 요청에 명시한 Peer 포함 |",
        f"| 관심 키워드 반영 | {sum(1 for r in done if not r['mentions_missing'])}/{len(done)} | 요청 관심사가 메모에 등장 |",
        f"| Peer 근거 커버리지 | {statistics.mean(cover) * 100:.0f}% | Peer 중 근거가 인용된 기업 비율 |" if cover else "| Peer 근거 커버리지 | – | |",
        f"| 재작성 발생 | {sum(1 for r in done if r['revisions'])}건 (총 {s('revisions')}회) | 검증 실패 → 재검색·재작성 |",
        f"| 최종 경고 문장 | {s('warnings')}개 | 재작성 후에도 검증 실패 |",
        f"| LLM 사용량 (Agent) | 질문당 평균 {s('llm_calls') / max(len(done), 1):.1f}회, 입력 {s('tokens_in') / max(len(done), 1):,.0f} / 출력 {s('tokens_out') / max(len(done), 1):,.0f} 토큰 | 캐시 재사용분 제외 |",
        (f"| LLM 사용량 (채점) | 입력 {judge_usage['input_tokens']:,} / 출력 {judge_usage['output_tokens']:,} 토큰 (캐시 재사용 {judge_usage['cached_calls']}회) | 평가 전체 합계 |"
         if judge_usage else "| LLM 채점 | 꺼짐 (코드 검증만) | `--judge` 또는 `--full`로 켬 |"),
        "",
        "## 메모 품질 (독립 모델 채점, 1~5점)",
        "",
        "| 항목 | 평균 | 기준 |",
        "|---|---|---|",
        *([f"| {k} | {statistics.mean([r['quality'].get(k, 0) for r in done if r.get('quality')]):.2f} | {v} |"
           for k, v in QUALITY_CRITERIA.items()] if any(r.get("quality") for r in done) else []),
        f"| **종합** | **{statistics.mean([r['quality_avg'] for r in done if r.get('quality_avg')]):.2f}** | 7개 항목 평균 |" if any(r.get("quality_avg") for r in done) else "",
        f"| 반복 문장 비율 | {statistics.mean([r.get('repetition', 0) for r in done]) * 100:.0f}% | 수치 문장 중 같은 틀(골격 80% 이상 일치) 비율 — 낮을수록 좋음 |" if done else "",
        f"| 최근 기간 언급 | {sum(1 for r in done if r.get('mentions_recent'))}/{len(done)} | 최근 12개월·최근 분기·전년 동기 언급 |" if done else "",
        "",
        "## 질문별 결과",
        "",
        "| ID | 대상 | Peer | 시간 | 문장 | 수치 | 인용 존재 | 근거(독립) | 재작성 | 품질 | 비고 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] != "ok":
            lines.append(f"| {r['id']} | – | – | {r['elapsed_s']}초 | – | – | – | – | – | – | 실패: {r.get('error', '')[:80]} |")
            continue
        note = []
        if r["mentions_missing"]:
            note.append(f"키워드 누락: {', '.join(r['mentions_missing'])}")
        if r["warnings"]:
            note.append(f"경고 {r['warnings']}문장")
        if r["number_errors"]:
            note.append(f"수치 오류 {len(r['number_errors'])}")
        lines.append(
            f"| {r['id']} | {r['target']} | {', '.join(r['peers'])} | {r['elapsed_s']:.0f}초 | {r['sentences']} | "
            f"{r['numbers_ok']}/{r['numbers_total']} | {r['citations_found']}/{r['citations']} | {r.get('judge_supported', '–')}/{r.get('judge_total', '–')} | "
            f"{r['revisions']} | {r.get('quality_avg', '–')} | {'; '.join(note) or '–'} |"
        )
    issues = [(r["id"], d) for r in done for d in r.get("judge_details", [])]
    if issues:
        lines += ["", "## 독립 재판정에서 지적된 문장", ""]
        lines += [f"- {qid} {d['sentence_id']} ({d['verdict']}): {d['reason']}" for qid, d in issues]
    worst = [(r["id"], r.get("quality_worst")) for r in done if r.get("quality_worst")]
    if worst:
        lines += ["", "## 품질 채점에서 지적된 가장 아쉬운 점", ""] + [f"- {qid}: {w}" for qid, w in worst]
    errs = [(r["id"], e) for r in done for e in r.get("number_errors", [])]
    if errs:
        lines += ["", "## 수치 불일치", ""] + [f"- {qid}: {e}" for qid, e in errs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


QUICK = ["q01", "q04", "q09"]  # 자동 Peer·수익성 악화 설명·지정 Peer — 서로 다른 경로를 하나씩


def main() -> None:
    """비용을 아끼는 기본값:
        python eval/run_eval.py            빠른 평가 — 3문항, LLM 채점 없음 (코드 검증만), LLM 캐시 사용
        python eval/run_eval.py --judge    + LLM 채점 (독립 재판정·품질 채점)
        python eval/run_eval.py --full     10문항 + LLM 채점 — 개선 효과를 확정할 때만
        python eval/run_eval.py q03 q05    지정 문항만
        python eval/run_eval.py --rescore  실행 없이 저장된 결과 다시 채점 (--judge와 함께 쓰면 채점만 추가)
    """
    import os

    os.environ.setdefault("PEERLENS_LLM_CACHE", "1")  # 같은 입력 재실행·재채점은 과금 없음
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rescore = "--rescore" in sys.argv
    full = "--full" in sys.argv
    judge = full or "--judge" in sys.argv
    questions = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))
    if args:
        questions = [q for q in questions if q["id"] in args]
    elif not full and not rescore:
        questions = [q for q in questions if q["id"] in QUICK]
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    llm = LLM()
    judge_llm = llm if judge else None
    print(f"평가: {len(questions)}문항 · LLM 채점 {'켬' if judge else '끔'} · 캐시 {os.environ['PEERLENS_LLM_CACHE']}", flush=True)
    runs_dir = ROOT.parent / "data" / "runs"
    rows = []
    for q in questions:
        if rescore:
            prev = sorted(out_dir.glob("*.json"))[-1]
            run_id = next(r["run_id"] for r in json.loads(prev.read_text(encoding="utf-8"))["rows"] if r["id"] == q["id"])
            result = json.loads((runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))
        else:
            print(f"\n[{q['id']}] {q['request']}", flush=True)
            t = time.perf_counter()
            result = run_agent(q["request"])
            print(f"  → {result['status']} {time.perf_counter() - t:.0f}초", flush=True)
        row = score(q, result, judge_llm)
        rows.append(row)
        print(f"  수치 {row.get('numbers_ok')}/{row.get('numbers_total')} · 인용 {row.get('citations_found')}/{row.get('citations')}"
              f" · 독립판정 {row.get('judge_supported')}/{row.get('judge_total')} · 키워드누락 {row.get('mentions_missing')}", flush=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out_dir / f"{stamp}.json").write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    report(rows, ROOT / "REPORT.md", {"main": llm.model, "fast": llm.fast_model}, judge_usage=llm.usage() if judge else None)
    agent_in = sum(r.get("tokens_in", 0) for r in rows if not rescore)
    agent_out = sum(r.get("tokens_out", 0) for r in rows if not rescore)
    ju = llm.usage()
    print(f"\n과금 토큰 — Agent 입력 {agent_in:,} / 출력 {agent_out:,} · 채점 입력 {ju['input_tokens']:,} / 출력 {ju['output_tokens']:,}"
          f" (캐시 재사용 {ju['cached_calls']}회)")
    print(f"보고서: eval/REPORT.md · 원본: eval/results/{stamp}.json")


if __name__ == "__main__":
    main()
