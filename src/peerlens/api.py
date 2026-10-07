"""FastAPI 앱: /api/* 는 JSON, 그 외 경로는 빌드된 웹 화면(web/dist)을 서빙한다.

    uvicorn peerlens.api:app --reload
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
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


# ---- Agent ----------------------------------------------------------------------

_AGENT_SLOTS = threading.Semaphore(int(os.getenv("PEERLENS_MAX_CONCURRENT_RUNS", "2")))  # 공개 데모 비용 보호
RUNS_DIR = PROJECT_ROOT / "data" / "runs"


@app.get("/api/agent/stream")
def agent_stream(q: str = Query(min_length=4, max_length=500)) -> StreamingResponse:
    """Agent 실행 과정을 Server-Sent Events로 실시간 전송. 마지막 이벤트(type=final)에 전체 결과."""
    from peerlens.agent import run_agent

    if not _AGENT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "다른 분석이 실행 중입니다. 잠시 후 다시 시도해 주세요.")
    events: queue.Queue[dict | None] = queue.Queue()

    def work() -> None:
        try:
            result = run_agent(q, events.put)
            events.put({"type": "final", "result": result})
        finally:
            _AGENT_SLOTS.release()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"  # 프록시 타임아웃 방지
                continue
            if ev is None:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/agent/runs")
def agent_runs(limit: int = Query(default=10, ge=1, le=50)) -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    out = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:limit]:
        r = json.loads(p.read_text(encoding="utf-8"))
        out.append({k: r.get(k) for k in ("run_id", "request", "status", "target", "peers", "elapsed_ms", "started_at")})
    return out


@app.get("/api/agent/runs/{run_id}")
def agent_run(run_id: str) -> dict:
    p = (RUNS_DIR / f"{run_id}.json").resolve()
    if not p.is_relative_to(RUNS_DIR.resolve()) or not p.exists():
        raise HTTPException(404, "실행 기록 없음")
    return json.loads(p.read_text(encoding="utf-8"))


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        file = (WEB_DIST / path).resolve()
        if path and file.is_file() and file.is_relative_to(WEB_DIST.resolve()):
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
