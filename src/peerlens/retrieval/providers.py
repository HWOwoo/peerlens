"""임베딩·리랭커 제공사 추상화. .env의 설정만 바꿔 다른 제공사·내부 모델로 교체할 수 있게 한다."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

InputType = Literal["search_document", "search_query"]


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...


@dataclass(frozen=True)
class RerankHit:
    index: int
    score: float


class Reranker(Protocol):
    model: str

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankHit]: ...


class _CohereBase:
    BASE = "https://api.cohere.com/v2"

    def __init__(self, api_key: str | None = None, max_retries: int = 5):
        key = api_key or os.getenv("COHERE_API_KEY", "")
        if not key:
            raise RuntimeError("COHERE_API_KEY가 .env에 없습니다.")
        self._http = httpx.Client(
            base_url=self.BASE,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=60.0,
        )
        self.max_retries = max_retries

    def _post(self, path: str, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            resp = self._http.post(path, json=body)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                time.sleep(min(2 ** attempt * 2, 60))  # 체험판 키 분당 한도 대응
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Cohere {path} {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise AssertionError("unreachable")


class CohereEmbedder(_CohereBase):
    BATCH = 96  # Cohere embed 1회 최대 입력 수

    def __init__(self, model: str | None = None, dim: int = 1024, **kw):
        super().__init__(**kw)
        self.model = model or os.getenv("COHERE_EMBED_MODEL", "embed-v4.0")
        self.dim = dim

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            data = self._post("/embed", {
                "model": self.model,
                "texts": texts[i : i + self.BATCH],
                "input_type": input_type,
                "embedding_types": ["float"],
                "output_dimension": self.dim,
            })
            out.extend(data["embeddings"]["float"])
        return out


class CohereReranker(_CohereBase):
    def __init__(self, model: str | None = None, **kw):
        super().__init__(**kw)
        self.model = model or os.getenv("COHERE_RERANK_MODEL", "rerank-v4.0-fast")

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankHit]:
        if not documents:
            return []
        data = self._post("/rerank", {"model": self.model, "query": query, "documents": documents, "top_n": top_n})
        return [RerankHit(r["index"], r["relevance_score"]) for r in data["results"]]


def default_embedder() -> Embedder:
    return CohereEmbedder()


def default_reranker() -> Reranker:
    return CohereReranker()
