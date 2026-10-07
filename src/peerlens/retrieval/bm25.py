"""키워드 검색용 BM25 희소 벡터. 모델이 아니라 단어 빈도 계산이라 서버에서 직접 처리한다.

문서 쪽은 BM25의 TF 항(포화·길이 정규화)만 계산하고, IDF는 Qdrant가 컬렉션 통계로 곱한다 (modifier=IDF).
"""

from __future__ import annotations

import re
import zlib
from collections import Counter

K1 = 1.2
B = 0.75
# 청크 크기를 chunking.MAX_CHARS로 통제하므로 평균 문서 길이는 고정값으로 둔다 (불용어 제외 토큰 수 기준).
# 코퍼스가 늘 때마다 재색인하지 않아도 되고, IDF는 Qdrant가 실제 통계로 계산한다.
AVGDL = 230.0

_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the their this to was were will with we our"
    " us may could would can such these those other which also not any all more than into".split()
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower().replace("’", "'")) if t not in _STOP]


def _index(token: str) -> int:
    return zlib.crc32(token.encode())  # 0 ~ 2^32-1, Qdrant 희소 인덱스(u32) 범위


def doc_vector(text: str, avgdl: float = AVGDL) -> tuple[list[int], list[float]]:
    toks = tokenize(text)
    if not toks:
        return [], []
    dl = len(toks)
    tf = Counter(toks)
    weights: dict[int, float] = {}
    for tok, f in tf.items():
        w = f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avgdl))
        idx = _index(tok)
        weights[idx] = weights.get(idx, 0.0) + w
    return list(weights), list(weights.values())


def query_vector(text: str) -> tuple[list[int], list[float]]:
    idx = sorted({_index(t) for t in tokenize(text)})
    return idx, [1.0] * len(idx)
