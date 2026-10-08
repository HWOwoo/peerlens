import dataclasses

import pytest
from fastapi.testclient import TestClient

from peerlens import service
from peerlens.api import app
from tests.test_metrics import _facts_for_metrics


@pytest.fixture
def client(monkeypatch):
    def fake_facts(ticker: str):
        scale = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0}[ticker]
        return tuple(
            dataclasses.replace(f, ticker=ticker, fact_id=f.fact_id.replace("T:", f"{ticker}:", 1), value=f.value * scale)
            for f in _facts_for_metrics()
        )

    monkeypatch.setattr(service, "company_facts", fake_facts)
    monkeypatch.setattr(service, "company_metrics", lambda t: tuple(service.compute_metrics(fake_facts(t))))
    return TestClient(app)


def test_compare_returns_cells_and_peer_median(client):
    r = client.post("/api/compare", json={"target": "aaa", "peers": ["BBB", "CCC", "AAA"], "years": 2, "align": "fiscal"})
    assert r.status_code == 200
    body = r.json()
    assert body["tickers"] == ["AAA", "BBB", "CCC"]
    assert body["years"] == [2024, 2025]
    med = next(p for p in body["peer_median"] if p["metric"] == "gross_margin" and p["year"] == 2025)
    assert med["n"] == 2 and med["median"] == pytest.approx(0.6)


def test_metric_detail_includes_input_facts_with_sources(client):
    d = client.get("/api/metrics/AAA:roe:2025-01-26").json()
    assert d["formula"].startswith("net_income")
    assert {f["fact_id"] for f in d["input_facts"]} == set(d["inputs"])
    assert all(f["filing_url"].startswith("https://www.sec.gov/Archives/") for f in d["input_facts"])


def test_unknown_metric_404(client):
    assert client.get("/api/metrics/AAA:roe:1999-01-01").status_code == 404


def test_request_validation(client):
    assert client.post("/api/compare", json={"target": "", "peers": []}).status_code == 422


def test_agent_stream_rejects_bad_peers(client):
    r = client.get("/api/agent/stream", params={"q": "NVDA 메모 써줘", "peers": "AMD,<script>"})
    assert r.status_code == 422
    r = client.get("/api/agent/stream", params={"q": "NVDA 메모 써줘", "peers": ",".join(f"T{i}" for i in range(9))})
    assert r.status_code == 422


def test_pdf_requires_finished_run(client, tmp_path, monkeypatch):
    import json

    from peerlens import api

    monkeypatch.setattr(api, "RUNS_DIR", tmp_path)
    (tmp_path / "r1.json").write_text(json.dumps({"run_id": "r1", "status": "error"}), encoding="utf-8")
    assert client.get("/api/agent/runs/r1/pdf").status_code == 409
    assert client.get("/api/agent/runs/nope/pdf").status_code == 404
