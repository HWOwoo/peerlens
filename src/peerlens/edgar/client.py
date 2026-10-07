"""SEC EDGAR HTTP 클라이언트.

SEC 접근 정책 준수:
- User-Agent에 이름+이메일 (Settings에서 강제)
- 초당 요청 수 상한 (기본 8, 최대 10)
- 모든 응답은 data/cache/ 에 저장하고, 캐시가 있으면 네트워크를 쓰지 않는다
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from peerlens.config import Settings

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{accn}-index.htm"

_RETRY_STATUS = {429, 500, 502, 503, 504}


def filing_index_url(cik: int, accn: str) -> str:
    return FILING_INDEX_URL.format(cik=cik, accn_nodash=accn.replace("-", ""), accn=accn)


@dataclass(frozen=True)
class CachedResponse:
    url: str
    retrieved_at: str  # ISO-8601 UTC, 출처 메타데이터의 '수집일'
    data: Any


class _RateLimiter:
    def __init__(self, max_rps: float):
        self._interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
            self._next = max(now, self._next) + self._interval


class EdgarClient:
    def __init__(self, settings: Settings | None = None, *, offline: bool = False, max_retries: int = 4):
        self.settings = settings or Settings.from_env()
        self.offline = offline
        self.max_retries = max_retries
        self._limiter = _RateLimiter(self.settings.sec_max_rps)
        self._http = httpx.Client(
            headers={"User-Agent": self.settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- public API -------------------------------------------------------

    def company_tickers(self, *, refresh: bool = False) -> CachedResponse:
        return self.get_json(COMPANY_TICKERS_URL, "company_tickers.json", refresh=refresh)

    def company_facts(self, cik: int, *, refresh: bool = False) -> CachedResponse:
        return self.get_json(COMPANYFACTS_URL.format(cik=cik), f"companyfacts/CIK{cik:010d}.json", refresh=refresh)

    def submissions(self, cik: int, *, refresh: bool = False) -> CachedResponse:
        return self.get_json(SUBMISSIONS_URL.format(cik=cik), f"submissions/CIK{cik:010d}.json", refresh=refresh)

    def resolve_ticker(self, ticker: str) -> tuple[int, str]:
        """티커 → (CIK, 회사명)."""
        rows = self.company_tickers().data.values()
        t = ticker.upper()
        for row in rows:
            if row["ticker"].upper() == t:
                return int(row["cik_str"]), row["title"]
        raise KeyError(f"Unknown ticker: {ticker}")

    # ---- cache + HTTP -----------------------------------------------------

    def get_json(self, url: str, cache_key: str, *, refresh: bool = False) -> CachedResponse:
        path = self.settings.cache_dir / "edgar" / cache_key
        if path.exists() and not refresh:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            return CachedResponse(envelope["url"], envelope["retrieved_at"], envelope["data"])
        if self.offline:
            raise FileNotFoundError(f"Offline mode and no cache for {url}")

        data = self._fetch(url).json()
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _atomic_write(path, json.dumps({"url": url, "retrieved_at": retrieved_at, "data": data}))
        return CachedResponse(url, retrieved_at, data)

    def get_text(self, url: str, cache_key: str, *, refresh: bool = False) -> CachedResponse:
        """공시 원문(HTML) 등 텍스트 응답. 본문은 원본 그대로, 수집일은 옆의 .meta.json에 저장."""
        path = self.settings.cache_dir / "edgar" / cache_key
        meta = path.with_name(path.name + ".meta.json")
        if path.exists() and meta.exists() and not refresh:
            m = json.loads(meta.read_text(encoding="utf-8"))
            return CachedResponse(m["url"], m["retrieved_at"], path.read_text(encoding="utf-8"))
        if self.offline:
            raise FileNotFoundError(f"Offline mode and no cache for {url}")

        resp = self._fetch(url)
        text = resp.content.decode(resp.encoding or "utf-8", errors="replace")
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _atomic_write(path, text)
        _atomic_write(meta, json.dumps({"url": url, "retrieved_at": retrieved_at}))
        return CachedResponse(url, retrieved_at, text)

    def _fetch(self, url: str) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                resp = self._http.get(url)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
            else:
                if resp.status_code not in _RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                if attempt == self.max_retries:
                    resp.raise_for_status()
            time.sleep(min(2**attempt, 30))  # 지수 백오프
        raise AssertionError("unreachable")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
