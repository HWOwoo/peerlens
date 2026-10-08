from peerlens.report import build_html


def _run():
    sent = lambda i, kind, segs, ev=(), status="pass": {  # noqa: E731
        "id": i, "kind": kind, "text": "", "evidence_ids": list(ev), "segments": segs,
        "status": status, "problems": ["자리표시자 밖 숫자"] if status == "fail" else [],
    }
    return {
        "run_id": "r1", "request": "AAA를 <Peer>와 비교", "target": "AAA", "peers": ["BBB"], "peer_override": ["BBB"],
        "started_at": "2026-10-08T01:02:03", "models": {"main": "m"},
        "memo": {
            "title": "AAA 검토",
            "blocks": [
                {"heading": "핵심 요약", "sentences": [
                    sent("s1", "quant", [{"type": "text", "text": "마진 "}, {"type": "metric", "text": "50.0%"}], ["E2"]),
                ]},
                {"heading": "리스크", "sentences": [sent("s2", "qual", [{"type": "text", "text": "수출 규제"}], ["E1", "E2"], "fail")]},
            ],
            "evidence": [
                {"ref_id": r, "company": "A Corp", "ticker": "AAA", "form": "10-K", "section_label": "위험 요인", "item": "1A",
                 "filed": "2026-02-01", "anchor_url": "https://sec.gov/x#:~:text=a", "source_url": "https://sec.gov/x", "text": f"인용 {r}"}
                for r in ("E1", "E2")
            ],
            "stats": {"quant": 1, "quant_pass": 1, "qual": 1, "qual_supported": 0, "revisions": 1, "warnings": 1},
        },
        "comparison": {
            "target": "AAA", "tickers": ["AAA", "BBB"], "metrics": [{"name": "net_margin", "label": "순이익률"}],
            "recent_cells": [{"ticker": t, "metric": "net_margin", "period_type": "TTM", "value": v} for t, v in (("AAA", 0.5), ("BBB", 0.1))],
            "recent_median": [{"metric": "net_margin", "period_type": "TTM", "median": 0.1}],
            "recent_periods": {"AAA": {"ttm": "2026-07 기준 12개월"}}, "stale": ["BBB"],
        },
        "peer_report": [{"ticker": "BBB", "name": "B Inc", "include": True, "score": 0.81, "reason": "같은 업종"}],
    }


def test_build_html_numbers_citations_and_escaping():
    h = build_html(_run())
    assert '<span class="num">50.0%</span><sup>[1]</sup>' in h  # 첫 인용(E2)이 1번
    assert "<sup>[2]</sup><sup>[1]</sup>" in h  # E1은 두 번째로 등장 → 2번
    assert "검증 경고" in h and "사용자가 Peer 구성 수정" in h
    assert "&lt;Peer&gt;" in h and "<Peer>" not in h  # 요청 문자열 이스케이프
    assert "50.0%" in h and "10.0%" in h and "오래된 데이터" in h
    assert h.count("#:~:text=a") == 2
