"""render_report: Agent 실행 결과 → 투자 검토 메모 PDF.

메모 문장의 수치는 이미 코드가 채운 값(segments)만 쓰고, 공시 인용은 각주 번호로 바꿔
마지막에 출처(공시 종류·섹션·공시일·원문 위치 링크)를 모은다. 화면과 같은 검증 정보를 함께 싣는다.

HTML을 만든 뒤 Playwright(Chromium)로 PDF를 찍는다. 한 번 만든 PDF는 data/reports/에 캐시.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from peerlens.config import PROJECT_ROOT

REPORTS_DIR = PROJECT_ROOT / "data" / "reports"

KIND = {"quant": "수치", "qual": "공시", "view": "의견"}
PCT_METRICS = ["revenue_growth", "gross_margin", "operating_margin", "net_margin", "roe", "rnd_intensity", "fcf_margin"]

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm; }
* { box-sizing: border-box; }
body { font-family: "Malgun Gothic", "Noto Sans CJK KR", "Apple SD Gothic Neo", sans-serif; color: #1b1b1b; font-size: 10pt; line-height: 1.6; }
h1 { font-size: 17pt; margin: 2mm 0 1mm; letter-spacing: -0.01em; }
h2 { font-size: 11.5pt; margin: 6mm 0 2mm; padding-bottom: 1mm; border-bottom: 1px solid #c9ced8; color: #1b2a4a; }
.brand { color: #2f73e8; font-weight: 700; font-size: 9pt; letter-spacing: 0.04em; }
.meta { color: #555; font-size: 8.5pt; }
.meta b { color: #1b1b1b; }
.req { background: #f3f6fb; border-radius: 2mm; padding: 2.5mm 3.5mm; margin: 3mm 0; font-size: 9.5pt; }
.stats { display: flex; gap: 2mm; margin: 3mm 0; }
.stat { flex: 1; border: 1px solid #d6dbe4; border-radius: 2mm; padding: 2mm 3mm; }
.stat .v { font-size: 13pt; font-weight: 700; }
.stat .l { font-size: 7.5pt; color: #666; }
.summary { background: #eef3fd; border-radius: 2mm; padding: 1mm 4mm; }
ul.s { list-style: none; margin: 0; padding: 0; }
ul.s li { margin: 1.4mm 0; padding-left: 9mm; position: relative; }
ul.s li .k { position: absolute; left: 0; top: 0.4mm; font-size: 7pt; color: #666; border: 1px solid #ccd; border-radius: 1mm; padding: 0 1mm; }
ul.s li .k.quant { color: #1c5cab; border-color: #9ec5f4; }
.num { font-weight: 700; color: #1c5cab; }
sup { color: #1c5cab; font-size: 7pt; }
.warn { color: #8a5a00; font-size: 8pt; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; margin-top: 1mm; }
th, td { border-bottom: 1px solid #e3e6ec; padding: 1.2mm 1.6mm; text-align: right; vertical-align: top; }
th { background: #1b2a4a; color: #fff; font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
td.t { background: #eef3fd; font-weight: 700; }
th small, td small { display: block; font-weight: 400; font-size: 6.8pt; color: #9aa; }
th small { color: #c8d3ea; }
td.reason { text-align: left; }
tr.out td { color: #999; }
ol.src { padding-left: 5mm; font-size: 8pt; }
ol.src li { margin: 1.5mm 0; break-inside: avoid; }
ol.src .q { color: #555; display: block; }
a { color: #1c5cab; text-decoration: none; word-break: break-all; }
.foot { margin-top: 6mm; font-size: 7.5pt; color: #777; border-top: 1px solid #ddd; padding-top: 2mm; }
.keep { break-inside: avoid; }
"""


def _e(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def _sentence(s: dict[str, Any], cite_no: dict[str, int]) -> str:
    body = "".join(
        f'<span class="num">{_e(seg["text"])}</span>' if seg["type"] == "metric" else _e(seg["text"]) for seg in s["segments"]
    )
    cites = "".join(f"<sup>[{cite_no[e]}]</sup>" for e in s["evidence_ids"] if e in cite_no)
    warn = f'<span class="warn">⚠ 검증 경고: {_e(s["problems"][0] if s["problems"] else "")}</span>' if s["status"] == "fail" else ""
    return f'<li><span class="k {s["kind"]}">{KIND[s["kind"]]}</span>{body}{cites}{warn}</li>'


def _comparison_table(comp: dict[str, Any]) -> str:
    cells = {(c["ticker"], c["metric"]): c for c in comp.get("recent_cells", []) if c["period_type"] == "TTM"}
    if not cells:
        return ""
    med = {m["metric"]: m["median"] for m in comp.get("recent_median", []) if m["period_type"] == "TTM"}
    labels = {m["name"]: m["label"] for m in comp["metrics"]}
    periods = comp.get("recent_periods", {})
    stale = set(comp.get("stale", []))
    head = "".join(
        f'<th>{_e(t)}<small>{_e((periods.get(t) or {}).get("ttm") or "")}{" · 오래된 데이터" if t in stale else ""}</small></th>'
        for t in comp["tickers"]
    )
    rows = []
    for m in PCT_METRICS:
        tds = "".join(
            f'<td class="{"t" if t == comp["target"] else ""}">{_pct(cells.get((t, m), {}).get("value"))}</td>' for t in comp["tickers"]
        )
        rows.append(f"<tr><td>{_e(labels.get(m, m))}</td>{tds}<td>{_pct(med.get(m))}</td></tr>")
    return (f"<table><thead><tr><th>지표 (최근 12개월)</th>{head}<th>Peer 중앙값<small>오래된 데이터 제외</small></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def _peer_table(report: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<tr class="{"" if p["include"] else "out"}"><td><b>{_e(p["ticker"])}</b> {_e(p.get("name") or "")}</td>'
        f'<td>{"포함" if p["include"] else "제외"}</td><td>{"" if p.get("score") is None else format(p["score"], ".2f")}</td>'
        f'<td class="reason">{_e(p["reason"])}</td></tr>'
        for p in report
    )
    return f"<table><thead><tr><th>기업</th><th>선정</th><th>점수</th><th>판단 이유</th></tr></thead><tbody>{rows}</tbody></table>"


def build_html(run: dict[str, Any]) -> str:
    memo, comp = run["memo"], run["comparison"]
    ev = {e["ref_id"]: e for e in memo["evidence"]}
    order: list[str] = []
    for b in memo["blocks"]:
        for s in b["sentences"]:
            for e in s["evidence_ids"]:
                if e in ev and e not in order:
                    order.append(e)
    cite_no = {e: i for i, e in enumerate(order, 1)}
    st = memo["stats"]
    blocks = []
    for b in memo["blocks"]:
        items = "".join(_sentence(s, cite_no) for s in b["sentences"])
        if b["heading"] == "핵심 요약":
            blocks.append(f'<div class="summary keep"><h2>핵심 요약</h2><ul class="s">{items}</ul></div>')
        else:
            blocks.append(f'<h2>{_e(b["heading"])}</h2><ul class="s">{items}</ul>')
    sources = "".join(
        f'<li><b>{_e(ev[e]["company"])} ({_e(ev[e]["ticker"])})</b> {_e(ev[e]["form"])} · {_e(ev[e]["section_label"])}'
        f'{" (Item " + _e(ev[e]["item"]) + ")" if ev[e].get("item") else ""} · 공시일 {_e(ev[e]["filed"])}<br>'
        f'<a href="{_e(ev[e]["anchor_url"])}">{_e(ev[e]["source_url"])}</a>'
        f'<span class="q">“{_e(ev[e]["text"][:220])}…”</span></li>'
        for e in order
    )
    peers = ", ".join(run.get("peers", []))
    period = (comp.get("recent_periods", {}).get(run["target"]) or {}).get("ttm") or ""
    edited = " · 사용자가 Peer 구성 수정" if run.get("peer_override") else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_e(memo['title'])}</title><style>{CSS}</style></head><body>
<div class="brand">PEERLENS · 공시 근거 기반 투자 검토 메모</div>
<h1>{_e(memo['title'])}</h1>
<div class="meta">대상 <b>{_e(run['target'])}</b> · Peer <b>{_e(peers)}</b>{edited} · 기준 {_e(period)} · 생성 {_e(run.get('started_at', '')[:16].replace('T', ' '))} UTC · 실행 {_e(run['run_id'])}</div>
<div class="req">요청: {_e(run['request'])}</div>
<div class="stats keep">
  <div class="stat"><div class="v">{st['quant_pass']}/{st['quant']}</div><div class="l">수치 문장 검증 (모든 수치 = SEC XBRL 계산값)</div></div>
  <div class="stat"><div class="v">{st['qual_supported']}/{st['qual']}</div><div class="l">근거 문장 일치 (LLM 판정)</div></div>
  <div class="stat"><div class="v">{st['revisions']}회</div><div class="l">검증 실패 → 재작성{f" · 경고 {st['warnings']}문장" if st['warnings'] else ""}</div></div>
  <div class="stat"><div class="v">{len(order)}건</div><div class="l">공시 원문 인용</div></div>
</div>
{''.join(blocks)}
<h2>재무 비교 (최근 12개월)</h2>
{_comparison_table(comp)}
<h2>Peer 선정 근거</h2>
{_peer_table(run.get('peer_report', []))}
<h2>출처</h2>
<ol class="src">{sources}</ol>
<div class="foot">수치는 SEC EDGAR XBRL 원값과 코드 계산값만 사용했으며 LLM이 숫자를 생성하지 않았습니다. 최근 12개월 = 최근 연간 + 올해 누적 − 작년 같은 기간 누적.
공시 인용은 원문 위치 링크로 확인할 수 있습니다. 본 문서는 투자 검토 초안이며 투자 권유가 아닙니다. 최종 투자 판단은 사용자 책임입니다.
· 모델 {_e(run.get('models', {}).get('main', ''))} · 생성 {datetime.now():%Y-%m-%d %H:%M}</div>
</body></html>"""


def render_pdf(run: dict[str, Any], *, force: bool = False) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{run['run_id']}.pdf"
    if out.exists() and not force:
        return out
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(build_html(run), wait_until="load")
        page.pdf(
            path=str(out), format="A4", print_background=True, display_header_footer=True,
            header_template="<span></span>",
            footer_template='<div style="font-size:7pt;color:#999;width:100%;text-align:center;">PeerLens · '
                            '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
            margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
        )
        browser.close()
    return out
