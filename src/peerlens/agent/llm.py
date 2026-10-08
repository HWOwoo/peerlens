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


def _provider() -> str:
    return (os.getenv("PEERLENS_LLM_PROVIDER") or "openai").strip().lower()


DEFAULT_MODELS = {
    "openai": ("OPENAI_MODEL", "gpt-6-sol", "OPENAI_MODEL_FAST", "gpt-6-luna"),
    "gemini": ("GEMINI_MODEL", "gemini-2.5-flash", "GEMINI_MODEL_FAST", "gemini-2.5-flash-lite"),
}


def _default_model(fast: bool) -> str:
    env_main, d_main, env_fast, d_fast = DEFAULT_MODELS[_provider()]
    return (os.getenv(env_fast) or d_fast) if fast else (os.getenv(env_main) or d_main)


class _Gemini:
    """Google Gemini (공식 google-genai SDK). 출력 형식은 pydantic 스키마로 강제.
    무료 등급의 분당 요청 한도에 대비해 호출 간격을 두고, 429면 기다렸다가 다시 시도한다."""

    _last = 0.0

    def __init__(self) -> None:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY가 .env에 없습니다.")
        from google import genai
        from google.genai import types

        # 기본값은 응답 대기 제한이 없어, 무료 등급 서버가 응답을 안 주면 무한정 멈춘다 → 요청당 제한(ms)
        timeout_ms = int(float(os.getenv("GEMINI_TIMEOUT_S", "120")) * 1000)
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
        self.min_interval = float(os.getenv("GEMINI_MIN_INTERVAL_S", "6") or 0)

    # 무료 일일 한도(모델별 하루 20회)가 소진된 모델: {모델: 재설정 시각(epoch)}. 다음 실행에서도 건너뛰도록 파일에 남긴다
    QUOTA_FILE = CACHE_DIR / "gemini_exhausted.json"

    def _exhausted(self) -> dict[str, float]:
        try:
            data = json.loads(self.QUOTA_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = time.time()
        return {m: t for m, t in data.items() if t > now}

    def _mark_exhausted(self, model: str, seconds: float) -> None:
        data = self._exhausted()
        data[model] = time.time() + seconds
        self.QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.QUOTA_FILE.write_text(json.dumps(data), encoding="utf-8")

    def parse(self, model: str, schema: type[T], system: str, user: str) -> tuple[T, int, int, str]:
        """(결과, 입력 토큰, 출력 토큰, 실제로 쓴 모델).
        과부하(503)·시간 초과 → 다음 모델 / 일일 한도 소진(429, 재시도 대기 > 2분) → 소진 기록 후 다음 모델 /
        분당 한도(429, 짧은 대기) → 최대 60초 기다렸다 같은 모델."""
        import re

        from google.genai import errors, types

        fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
        config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json", response_schema=schema)
        i = 0
        for attempt in range(12):
            exhausted = self._exhausted()
            chain = [m for m in dict.fromkeys([model, *fallbacks]) if m not in exhausted]
            if not chain:
                reset = min(exhausted.values()) - time.time()
                raise RuntimeError(f"Gemini 무료 일일 한도 소진 (모든 대체 모델). 가장 빠른 재설정까지 약 {reset / 3600:.1f}시간")
            current = chain[i % len(chain)]
            wait = _Gemini._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _Gemini._last = time.monotonic()
            try:
                resp = self.client.models.generate_content(model=current, contents=user, config=config)
            except Exception as e:
                if not isinstance(e, errors.APIError):
                    # 시간 초과·연결 끊김 → 과부하와 같이 다음 모델로
                    if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() or "connect" in type(e).__name__.lower():
                        i += 1
                        continue
                    raise
                code = getattr(e, "code", None)
                if code in (500, 503):  # 과부하 → 다음 모델
                    i += 1
                    if i % len(chain) == 0:  # 한 바퀴 돌았으면 잠시 쉰다
                        time.sleep(min(10 * 2 ** (i // len(chain)), 60))
                    continue
                if code == 429:
                    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)", str(e))
                    delay = int(m.group(1)) if m else 30
                    if delay > 120 or "PerDay" in str(e):  # 일일 한도 → 기다리지 않고 다음 모델
                        self._mark_exhausted(current, delay)
                        continue
                    time.sleep(min(delay + 1, 60))  # 분당 한도
                    continue
                raise
            parsed = resp.parsed if isinstance(resp.parsed, schema) else None
            if parsed is None:
                try:
                    parsed = schema.model_validate_json(resp.text or "")
                except Exception:
                    if attempt < 3:
                        continue  # 형식이 깨지면 한 번 더
                    raise RuntimeError(f"Gemini 응답이 형식에 맞지 않음: {(resp.text or '')[:200]}")
            u = resp.usage_metadata
            out_tokens = (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0) if u else 0
            return parsed, (u.prompt_token_count or 0) if u else 0, out_tokens, current
        raise RuntimeError("Gemini 호출 재시도 초과")


@dataclass
class LLM:
    model: str = field(default_factory=lambda: _default_model(fast=False))
    fast_model: str = field(default_factory=lambda: _default_model(fast=True))
    calls: list[LLMCall] = field(default_factory=list)
    use_cache: bool = field(default_factory=lambda: _flag("PEERLENS_LLM_CACHE"))
    budget: int = field(default_factory=lambda: int(os.getenv("PEERLENS_RUN_TOKEN_BUDGET", "0") or 0))
    provider: str = field(default_factory=_provider)

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

    def _call(self, model: str, schema: type[T], system: str, user: str, name: str) -> tuple[T, int, int, str]:
        if self.provider == "gemini":
            if self._client is None:
                self._client = _Gemini()
            return self._client.parse(model, schema, system, user)
        resp = self._openai().responses.parse(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=schema,
        )
        if resp.output_parsed is None:
            raise RuntimeError(f"{name}: 모델이 형식에 맞는 답을 내지 않음 (refusal 또는 빈 응답)")
        u = resp.usage
        return resp.output_parsed, (u.input_tokens if u else 0), (u.output_tokens if u else 0), model

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
        parsed, tin, tout, used = self._call(model, schema, system, user, name)
        call = LLMCall(name, used, tin, tout, round((time.perf_counter() - t0) * 1000))
        self.calls.append(call)
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"name": name, "model": model, "input_tokens": call.input_tokens,
                                        "output_tokens": call.output_tokens, "output": parsed.model_dump()},
                                       ensure_ascii=False), encoding="utf-8")
        return parsed, call

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
