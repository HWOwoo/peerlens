"""Agent 핵심 규칙 테스트 — LLM은 가짜로 대체 (네트워크·API 키 불필요)."""

import pytest

from peerlens.agent import graph
from peerlens.agent.llm import LLMCall
from peerlens.agent.refs import RefTable, build_metric_refs, render_text, segments, stray_numbers
from peerlens.agent.schemas import Judgement, Judgements, Revision, SentencePatch

COMPARISON = {
    "target": "AAA",
    "tickers": ["AAA", "BBB", "CCC"],
    "year_label": "CY",
    "years": [2025],
    "latest_year": 2025,
    "metrics": [{"name": "operating_margin", "label": "영업이익률", "formula": "operating_income / revenue"}],
    "cells": [
        {"ticker": "AAA", "metric": "operating_margin", "year": 2025, "value": 0.6, "metric_id": "AAA:operating_margin:2025-12-31", "flags": []},
        {"ticker": "BBB", "metric": "operating_margin", "year": 2025, "value": 0.2, "metric_id": "BBB:operating_margin:2025-12-31", "flags": []},
    ],
    "peer_median": [{"metric": "operating_margin", "year": 2025, "n": 1, "median": 0.2, "formula": "median"}],
    "missing_latest": [],
}

HIT = {"chunk_id": "AAA:x:risk:0001", "ticker": "AAA", "form": "10-K", "section_label": "위험 요인", "subheading": "",
       "text": "Sales to China are subject to export controls and licenses."}


def test_metric_refs_include_company_median_and_diff():
    refs = build_metric_refs(COMPARISON)
    assert refs["AAA.operating_margin.2025"].display == "60.0%"
    assert refs["PEER.operating_margin.2025"].display == "20.0%"
    assert refs["DIFF.operating_margin.2025"].display == "+40.0%p"
    assert refs["DIFF.operating_margin.2025"].metric_ids == ["AAA:operating_margin:2025-12-31"]


@pytest.mark.parametrize("text,stray", [
    ("영업이익률은 [[AAA.operating_margin.2025]]로 높다.", []),
    ("FY2025 10-K Item 1A에 따르면 2025년 Q3에 변화가 있었다.", []),
    ("영업이익률은 60%로 높다.", ["60%"]),
    ("매출은 1,234억 달러다.", ["1,234"]),
    ("최근 12개월 기준 3년간 개선됐고, Intel 18A·H100·HBM3E·5G 제품이 거론됐다.", []),  # 기간·제품명은 허용
    ("매출은 $5B, 3x 성장했다.", ["5", "3"]),  # 금액·배수 단위는 여전히 금지
])
def test_stray_numbers(text, stray):
    assert [s.strip() for s in stray_numbers(text)] == stray


def test_render_and_segments():
    t = RefTable(metrics=build_metric_refs(COMPARISON))
    text = "AAA는 Peer보다 [[DIFF.operating_margin.2025]] 높다."
    assert render_text(text, t)[0] == "AAA는 Peer보다 +40.0%p 높다."
    segs = segments(text, t)
    assert [s["type"] for s in segs] == ["text", "metric", "text"]
    assert segs[1]["metric_ids"] == ["AAA:operating_margin:2025-12-31"]
    assert render_text("[[NOPE.x.1]]", t)[0] == "⟦NOPE.x.1?⟧"


class FakeLLM:
    model, fast_model = "fake-main", "fake-fast"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, schema, *, system, user, name, fast=False):
        self.calls.append((name, user))
        out = self.responses.pop(0)
        assert isinstance(out, schema)
        return out, LLMCall(name, self.fast_model if fast else self.model, 10, 5, 1)

    def usage(self):
        return {"calls": len(self.calls), "input_tokens": 0, "output_tokens": 0}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    c = graph.Ctx(lambda e: None)
    c.refs.metrics = build_metric_refs(COMPARISON)
    c.refs.add_evidence("q", HIT)
    return c


def _memo(*sentences):
    return {"title": "t", "blocks": [{"heading": "h", "sentences": [{"id": f"s{i + 1}", **s} for i, s in enumerate(sentences)]}]}


def test_verify_rule_checks_and_judge(ctx):
    ctx.llm = FakeLLM([Judgements(items=[Judgement(sentence_id="s4", verdict="unsupported", reason="근거에 없음")])])
    memo = _memo(
        {"kind": "quant", "text": "AAA 영업이익률은 [[AAA.operating_margin.2025]]이다.", "evidence_ids": []},  # 통과
        {"kind": "quant", "text": "AAA 영업이익률은 60%이다.", "evidence_ids": []},  # 숫자 직접 작성
        {"kind": "qual", "text": "AAA는 수출 통제를 받는다.", "evidence_ids": []},  # 근거 없음
        {"kind": "qual", "text": "AAA는 중국 매출이 늘었다.", "evidence_ids": ["E1"]},  # LLM 판정 실패
        {"kind": "view", "text": "중국 리스크 점검이 필요하다.", "evidence_ids": ["E9"]},  # 없는 근거 ID
    )
    out = graph.verify_node({"memo": memo}, ctx)
    st = {k: v["status"] for k, v in out["checks"].items()}
    assert st == {"s1": "pass", "s2": "fail", "s3": "fail", "s4": "fail", "s5": "fail"}
    assert "숫자를 직접" in out["checks"]["s2"]["problems"][0]
    assert out["checks"]["s4"]["verdict"] == "unsupported"
    judged_prompt = ctx.llm.calls[0][1]
    assert "s4" in judged_prompt and "s1" not in judged_prompt  # 규칙 통과한 qual 문장만 LLM 판정


def test_route_stops_after_max_revisions():
    fail = {"s1": {"status": "fail"}}
    assert graph.route_after_verify({"checks": fail, "attempt": 0}) == "revise"
    assert graph.route_after_verify({"checks": fail, "attempt": graph.MAX_REVISIONS}) == "finalize"
    assert graph.route_after_verify({"checks": {"s1": {"status": "pass"}}, "attempt": 0}) == "finalize"


def test_revise_patches_only_failed_sentences(ctx, monkeypatch):
    monkeypatch.setattr(graph.service, "search_evidence", lambda *a, **k: {"results": [], "elapsed_ms": 1})
    ctx.llm = FakeLLM([Revision(patches=[
        SentencePatch(sentence_id="s2", action="replace", kind="quant", text="[[AAA.operating_margin.2025]]", evidence_ids=[]),
        SentencePatch(sentence_id="s3", action="drop", kind="qual", text="", evidence_ids=[]),
        SentencePatch(sentence_id="s1", action="drop", kind="quant", text="", evidence_ids=[]),  # 통과 문장은 무시돼야 함
    ])])
    memo = _memo(
        {"kind": "quant", "text": "ok [[AAA.operating_margin.2025]]", "evidence_ids": []},
        {"kind": "quant", "text": "60%", "evidence_ids": []},
        {"kind": "qual", "text": "근거 없음", "evidence_ids": []},
    )
    checks = {"s1": {"status": "pass", "problems": [], "verdict": None},
              "s2": {"status": "fail", "problems": ["숫자"], "verdict": None},
              "s3": {"status": "fail", "problems": ["근거 없음"], "verdict": None}}
    out = graph.revise_node({"memo": memo, "checks": checks, "attempt": 0, "comparison": COMPARISON, "target": "AAA"}, ctx)
    ids = [s["id"] for s in out["memo"]["blocks"][0]["sentences"]]
    assert ids == ["s1", "s2"] and out["attempt"] == 1
    assert out["memo"]["blocks"][0]["sentences"][1]["revised"] == 1


def test_llm_cache_reuses_identical_calls(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from peerlens.agent import llm as llm_mod
    from peerlens.agent.schemas import Judgements as J

    monkeypatch.setattr(llm_mod, "CACHE_DIR", tmp_path)
    calls = []

    class FakeResponses:
        def parse(self, **kw):
            calls.append(kw)
            return SimpleNamespace(output_parsed=J(items=[]), usage=SimpleNamespace(input_tokens=100, output_tokens=10))

    m = llm_mod.LLM(model="main", fast_model="fast", use_cache=True, budget=150, provider="openai")
    m._client = SimpleNamespace(responses=FakeResponses())
    _, c1 = m.parse(J, system="s", user="u", name="x")
    _, c2 = m.parse(J, system="s", user="u", name="x")
    _, c3 = m.parse(J, system="s", user="other", name="x")
    assert len(calls) == 2 and not c1.cached and c2.cached and not c3.cached
    assert m.usage()["cached_calls"] == 1 and m.spent() == 220
    assert m.over_budget()  # 220 >= 150


def test_dev_profile_uses_fast_model_everywhere(monkeypatch):
    from peerlens.agent import llm as llm_mod

    monkeypatch.setenv("PEERLENS_PROFILE", "dev")
    m = llm_mod.LLM(model="main", fast_model="fast")
    assert m.model == "fast"


def test_gemini_daily_quota_skips_model_without_waiting(tmp_path, monkeypatch):
    """일일 한도(429, retryDelay 22시간)면 기다리지 않고 소진 기록 후 다음 모델로."""
    import time
    from types import SimpleNamespace

    from google.genai import errors

    from peerlens.agent import llm as llm_mod
    from peerlens.agent.schemas import Judgements as J

    monkeypatch.setattr(llm_mod._Gemini, "QUOTA_FILE", tmp_path / "q.json")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2")
    used = []

    class Models:
        def generate_content(self, model, contents, config):
            used.append(model)
            if model == "m1":
                raise errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                          "message": "quota GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                                          "details": [{"retryDelay": "79114s"}]}})
            return SimpleNamespace(parsed=J(items=[]), text="{}", usage_metadata=SimpleNamespace(
                prompt_token_count=5, candidates_token_count=1, thoughts_token_count=0))

    g = llm_mod._Gemini.__new__(llm_mod._Gemini)
    g.client, g.min_interval = SimpleNamespace(models=Models()), 0
    t = time.time()
    _, _, _, model = g.parse("m1", J, "s", "u")
    assert model == "m2" and used == ["m1", "m2"] and time.time() - t < 5
    assert "m1" in g._exhausted()
    g.parse("m1", J, "s", "u")  # 다음 호출은 소진된 m1을 아예 건너뜀
    assert used == ["m1", "m2", "m2"]


def test_stray_allows_process_names_and_ordinals_but_not_counts():
    assert stray_numbers("Intel 7 공정과 Intel 18A, 제3자 공급망") == []
    assert stray_numbers("300mm 웨이퍼, 3nm 공정, 2.5D 패키징") == []
    assert [x.strip() for x in stray_numbers("매출 $300M, 5M 달러")] == ["300", "5"]  # 금액 단위 M은 여전히 금지
    assert [x.strip() for x in stray_numbers("아날로그 4개사를 비교")] == ["4"]  # 기업 수는 [[COUNT…]]로


def test_masked_text_hides_verified_numbers_for_judge():
    from peerlens.agent.refs import masked_text

    assert masked_text("영업이익률 [[AAA.operating_margin.TTM]]로 [[RANK.operating_margin.TTM]]") == "영업이익률 ⟨수치⟩로 ⟨수치⟩"
