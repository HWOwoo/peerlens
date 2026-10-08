"""LLM 호출 추상화. 모델은 .env(OPENAI_MODEL, OPENAI_MODEL_FAST)로 교체한다.

모든 호출은 JSON 스키마(pydantic)로 출력 형식을 강제하고, 토큰·시간을 Trace에 남긴다.

비용 관리 (.env):
- PEERLENS_LLM_CACHE=1   같은 입력(모델·지시문·내용·출력 형식)이면 저장된 응답을 재사용 → 재실행·재채점 비용 0
- PEERLENS_PROFILE=dev   모든 단계를 빠른(저가) 모델로 — 코드 수정 확인용
- PEERLENS_RUN_TOKEN_BUDGET  실행 1건의 입력+출력 토큰 상한. 넘으면 재작성 같은 선택 단계를 건너뛴다
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from peerlens.config import PROJECT_ROOT

T = TypeVar("T", bound=BaseModel)

CACHE_DIR = PROJECT_ROOT / "data" / "llm_cache"


@dataclass
class LLMCall:
    name: str
    model: str
    input_tokens: int
    output_tokens: int
    ms: int
    cached: bool = False


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class LLM:
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL") or "gpt-6-sol")
    fast_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL_FAST") or "gpt-6-luna")
    calls: list[LLMCall] = field(default_factory=list)
    use_cache: bool = field(default_factory=lambda: _flag("PEERLENS_LLM_CACHE"))
    budget: int = field(default_factory=lambda: int(os.getenv("PEERLENS_RUN_TOKEN_BUDGET", "0") or 0))

    def __post_init__(self) -> None:
        if os.getenv("PEERLENS_PROFILE", "").lower() == "dev":
            self.model = self.fast_model  # 개발 모드: 전부 저가 모델
        self._client = None

    def _openai(self):
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY가 .env에 없습니다.")
            from openai import OpenAI

            self._client = OpenAI(max_retries=3, timeout=180)
        return self._client

    @staticmethod
    def _key(model: str, schema: type[BaseModel], system: str, user: str) -> str:
        blob = json.dumps({"m": model, "s": system, "u": user, "f": schema.model_json_schema()}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def parse(self, schema: type[T], *, system: str, user: str, name: str, fast: bool = False) -> tuple[T, LLMCall]:
        model = self.fast_model if fast else self.model
        key = self._key(model, schema, system, user)
        path = CACHE_DIR / key[:2] / f"{key}.json"
        if self.use_cache and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            call = LLMCall(name, model, data["input_tokens"], data["output_tokens"], 0, cached=True)
            self.calls.append(call)
            return schema.model_validate(data["output"]), call

        t0 = time.perf_counter()
        resp = self._openai().responses.parse(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=schema,
        )
        if resp.output_parsed is None:
            raise RuntimeError(f"{name}: 모델이 형식에 맞는 답을 내지 않음 (refusal 또는 빈 응답)")
        u = resp.usage
        call = LLMCall(name, model, u.input_tokens if u else 0, u.output_tokens if u else 0, round((time.perf_counter() - t0) * 1000))
        self.calls.append(call)
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"name": name, "model": model, "input_tokens": call.input_tokens,
                                        "output_tokens": call.output_tokens, "output": resp.output_parsed.model_dump()},
                                       ensure_ascii=False), encoding="utf-8")
        return resp.output_parsed, call

    def spent(self) -> int:
        """실제로 과금된(캐시 아닌) 입력+출력 토큰."""
        return sum(c.input_tokens + c.output_tokens for c in self.calls if not c.cached)

    def over_budget(self) -> bool:
        return bool(self.budget) and self.spent() >= self.budget

    def usage(self) -> dict[str, int]:
        billed = [c for c in self.calls if not c.cached]
        by_model: dict[str, dict[str, int]] = {}
        for c in billed:
            m = by_model.setdefault(c.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += c.input_tokens
            m["output_tokens"] += c.output_tokens
        return {
            "calls": len(self.calls),
            "cached_calls": len(self.calls) - len(billed),
            "input_tokens": sum(c.input_tokens for c in billed),
            "output_tokens": sum(c.output_tokens for c in billed),
            "by_model": by_model,
        }
