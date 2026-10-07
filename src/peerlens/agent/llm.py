"""LLM 호출 추상화. 모델은 .env(OPENAI_MODEL, OPENAI_MODEL_FAST)로 교체한다.

모든 호출은 JSON 스키마(pydantic)로 출력 형식을 강제하고, 토큰·시간을 Trace에 남긴다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCall:
    name: str
    model: str
    input_tokens: int
    output_tokens: int
    ms: int


@dataclass
class LLM:
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL") or "gpt-6-sol")
    fast_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL_FAST") or "gpt-6-luna")
    calls: list[LLMCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY가 .env에 없습니다.")
        self._client = OpenAI(max_retries=3, timeout=180)

    def parse(self, schema: type[T], *, system: str, user: str, name: str, fast: bool = False) -> tuple[T, LLMCall]:
        model = self.fast_model if fast else self.model
        t0 = time.perf_counter()
        resp = self._client.responses.parse(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=schema,
        )
        if resp.output_parsed is None:
            raise RuntimeError(f"{name}: 모델이 형식에 맞는 답을 내지 않음 (refusal 또는 빈 응답)")
        u = resp.usage
        call = LLMCall(name, model, u.input_tokens if u else 0, u.output_tokens if u else 0, round((time.perf_counter() - t0) * 1000))
        self.calls.append(call)
        return resp.output_parsed, call

    def usage(self) -> dict[str, int]:
        return {
            "calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
        }
