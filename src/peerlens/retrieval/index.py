"""공시 원문 하이브리드 검색: Qdrant(dense: 외부 임베딩 API, sparse: BM25) → RRF 결합 → 외부 리랭커.

search_filings Tool이 이 클래스를 감싼다. 결과마다 각 단계 순위를 남겨 Trace 화면에서 검색 과정을 보여줄 수 있게 한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from qdrant_client import QdrantClient, models

from peerlens.config import PROJECT_ROOT
from peerlens.retrieval import bm25
from peerlens.retrieval.chunking import Chunk
from peerlens.retrieval.providers import Embedder, Reranker

COLLECTION = "filings"
RRF_K = 60


@dataclass
class Evidence:
    chunk: dict[str, Any]  # Chunk payload (출처 메타데이터 포함)
    score: float  # 리랭커 관련도 (0~1)
    rank: int
    fused_rank: int
    dense_rank: int | None
    sparse_rank: int | None
    trace: dict[str, Any] = field(default_factory=dict)


def rrf(rankings: Iterable[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: 여러 검색 결과 순위를 1/(k+rank) 합으로 결합."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for r, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + r)
    return sorted(scores.items(), key=lambda x: -x[1])


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class FilingIndex:
    def __init__(
        self,
        embedder: Embedder,
        reranker: Reranker | None,
        *,
        path: Path | None = None,
        client: QdrantClient | None = None,
        collection: str = COLLECTION,
    ):
        self.embedder = embedder
        self.reranker = reranker
        self.collection = collection
        self.qdrant = client or QdrantClient(path=str(path or PROJECT_ROOT / "data" / "qdrant"))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self.qdrant.collection_exists(self.collection):
            return
        self.qdrant.create_collection(
            self.collection,
            vectors_config={"dense": models.VectorParams(size=self.embedder.dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )

    # ---- 색인 ---------------------------------------------------------------

    def index_chunks(self, chunks: list[Chunk]) -> tuple[int, int]:
        """(새로 넣은 수, 이미 있어 건너뛴 수). 같은 청크·같은 본문이면 임베딩 API를 다시 부르지 않는다."""
        ids = [point_id(c.chunk_id) for c in chunks]
        # 같은 공시를 다시 색인할 때 이번 결과에 없는 옛 청크는 지운다 (청크 수가 줄어든 경우)
        for accn in {c.accn for c in chunks}:
            self.qdrant.delete(self.collection, points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(key="accn", match=models.MatchValue(value=accn))],
                must_not=[models.HasIdCondition(has_id=ids)],
            )))
        existing = {
            p.id: (p.payload or {}).get("text_hash")
            for p in self.qdrant.retrieve(self.collection, ids=ids, with_payload=["text_hash"], with_vectors=False)
        }
        todo = [c for c, pid in zip(chunks, ids) if existing.get(pid) != c.text_hash]
        if not todo:
            return 0, len(chunks)
        vectors = self.embedder.embed([c.embed_text for c in todo], "search_document")
        points = []
        for c, vec in zip(todo, vectors):
            idx, val = bm25.doc_vector(c.embed_text)
            points.append(models.PointStruct(
                id=point_id(c.chunk_id),
                vector={"dense": vec, "bm25": models.SparseVector(indices=idx, values=val)},
                payload={**c.to_payload(), "embed_model": self.embedder.model},
            ))
        for i in range(0, len(points), 128):
            self.qdrant.upsert(self.collection, points=points[i : i + 128])
        return len(todo), len(chunks) - len(todo)

    def count(self, ticker: str | None = None) -> int:
        flt = _filter([ticker] if ticker else None, None)
        return self.qdrant.count(self.collection, count_filter=flt, exact=True).count

    def indexed_filings(self) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        offset = None
        while True:
            pts, offset = self.qdrant.scroll(
                self.collection, limit=512, offset=offset,
                with_payload=["ticker", "form", "accn", "filed", "report_date", "section"], with_vectors=False,
            )
            for p in pts:
                pl = p.payload or {}
                f = seen.setdefault(pl["accn"], {k: pl[k] for k in ("ticker", "form", "accn", "filed", "report_date")} | {"chunks": 0, "sections": set()})
                f["chunks"] += 1
                f["sections"].add(pl["section"])
            if offset is None:
                break
        return [{**f, "sections": sorted(f["sections"])} for f in seen.values()]

    # ---- 검색 ---------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        tickers: list[str] | None = None,
        sections: list[str] | None = None,
        k: int = 5,
        candidates: int = 40,
        rerank_pool: int = 25,
        keyword_query: str | None = None,
    ) -> list[Evidence]:
        """keyword_query: BM25용 영어 키워드 (한국어 질문이면 Agent가 영어 키워드를 따로 넘긴다)."""
        flt = _filter(tickers, sections)
        dense_vec = self.embedder.embed([query], "search_query")[0]
        dense = self.qdrant.query_points(self.collection, query=dense_vec, using="dense", query_filter=flt, limit=candidates, with_payload=False).points
        s_idx, s_val = bm25.query_vector(keyword_query or query)
        sparse = (
            self.qdrant.query_points(
                self.collection, query=models.SparseVector(indices=s_idx, values=s_val), using="bm25",
                query_filter=flt, limit=candidates, with_payload=False,
            ).points
            if s_idx else []
        )
        dense_ids = [str(p.id) for p in dense]
        sparse_ids = [str(p.id) for p in sparse]
        fused = rrf([dense_ids, sparse_ids])[:rerank_pool]
        if not fused:
            return []
        payloads = {str(p.id): p.payload for p in self.qdrant.retrieve(self.collection, ids=[pid for pid, _ in fused], with_payload=True)}
        # 같은 문단이 여러 섹션에 반복 공시되는 경우(사업 개요·위험 요인 등) 하나만 남긴다
        pool, seen_text = [], set()
        for pid, _ in fused:
            pl = payloads.get(pid)
            if pl is None:
                continue
            key = (pl["ticker"], " ".join(pl["text"].split())[:400])
            if key in seen_text:
                continue
            seen_text.add(key)
            pool.append((pid, pl))

        def ranks(pid: str) -> tuple[int, int | None, int | None]:
            fr = next(i for i, (p, _) in enumerate(fused, 1) if p == pid)
            return fr, (dense_ids.index(pid) + 1 if pid in dense_ids else None), (sparse_ids.index(pid) + 1 if pid in sparse_ids else None)

        if self.reranker is None:
            ordered = [(pid, pl, sc) for (pid, pl), (_, sc) in zip(pool, fused)][:k]
        else:
            docs = [_rerank_text(pl) for _, pl in pool]
            hits = self.reranker.rerank(query, docs, top_n=min(k, len(docs)))
            ordered = [(pool[h.index][0], pool[h.index][1], h.score) for h in hits]

        out = []
        for rank, (pid, pl, sc) in enumerate(ordered, 1):
            fr, dr, sr = ranks(pid)
            out.append(Evidence(chunk=pl, score=float(sc), rank=rank, fused_rank=fr, dense_rank=dr, sparse_rank=sr))
        return out


def _rerank_text(pl: dict[str, Any]) -> str:
    head = f"{pl['company']} ({pl['ticker']}) {pl['form']} — {pl['section_label']}"
    if pl.get("subheading"):
        head += f" — {pl['subheading']}"
    return f"{head}\n{pl['text']}"


def _filter(tickers: list[str] | None, sections: list[str] | None) -> models.Filter | None:
    must: list[models.Condition] = []
    if tickers:
        must.append(models.FieldCondition(key="ticker", match=models.MatchAny(any=[t.upper() for t in tickers])))
    if sections:
        must.append(models.FieldCondition(key="section", match=models.MatchAny(any=sections)))
    return models.Filter(must=must) if must else None
