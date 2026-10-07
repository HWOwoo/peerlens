"""FastAPI 앱: /api/* 는 JSON, 그 외 경로는 빌드된 웹 화면(web/dist)을 서빙한다.

    uvicorn peerlens.api:app --reload
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from peerlens import service
from peerlens.config import PROJECT_ROOT

app = FastAPI(title="PeerLens API", version="0.1.0")

WEB_DIST = Path(os.getenv("PEERLENS_WEB_DIST", PROJECT_ROOT / "web" / "dist"))


class CompareRequest(BaseModel):
    target: str = Field(min_length=1, max_length=10)
    peers: list[str] = Field(default_factory=list, max_length=12)
    years: int = Field(default=3, ge=1, le=10)
    align: Literal["calendar", "fiscal"] = "calendar"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/companies")
def companies(q: str = Query(min_length=1, max_length=50)) -> list[dict]:
    return service.search_companies(q)


@app.post("/api/compare")
def compare(req: CompareRequest) -> dict:
    try:
        return service.compare(req.target, req.peers, years=req.years, align=req.align)
    except KeyError as e:
        raise HTTPException(404, f"알 수 없는 티커: {e.args[0]}") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/metrics/{metric_id}")
def metric(metric_id: str) -> dict:
    try:
        return service.metric_detail(metric_id)
    except KeyError as e:
        raise HTTPException(404, f"지표를 찾을 수 없음: {metric_id}") from e


SECTIONS = {"business", "risk_factors", "mdna", "market_risk"}


def _csv(v: str | None) -> list[str] | None:
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    return items or None


@app.get("/api/evidence")
def evidence(
    q: str = Query(min_length=2, max_length=300),
    tickers: str | None = Query(default=None, description="쉼표 구분 티커"),
    sections: str | None = Query(default=None, description="business,risk_factors,mdna,market_risk"),
    k: int = Query(default=5, ge=1, le=10),
    keywords: str | None = Query(default=None, max_length=200, description="키워드 검색용 영어 질의"),
) -> dict:
    secs = _csv(sections)
    if secs and not set(secs) <= SECTIONS:
        raise HTTPException(422, f"sections는 {sorted(SECTIONS)} 중에서")
    try:
        return service.search_evidence(q, tickers=_csv(tickers), sections=secs, k=k, keywords=keywords)
    except RuntimeError as e:  # API 키 누락·제공사 오류
        raise HTTPException(503, str(e)) from e


@app.get("/api/filings")
def filings() -> list[dict]:
    return service.indexed_filings()


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        file = (WEB_DIST / path).resolve()
        if path and file.is_file() and file.is_relative_to(WEB_DIST.resolve()):
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
