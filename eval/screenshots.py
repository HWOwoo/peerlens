"""기획서·발표자료용 화면 캡처 (Playwright). 웹 서버가 켜져 있어야 한다.

    python eval/screenshots.py [실행ID] [--base http://localhost:8000]

결과: docs/screenshots/*.png (2배 해상도)
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def shot(page: Page, name: str, selector: str | None = None, full: bool = False) -> None:
    path = OUT / f"{name}.png"
    if selector:
        page.locator(selector).first.screenshot(path=path)
    else:
        page.screenshot(path=path, full_page=full)
    print("저장", path.relative_to(OUT.parents[1]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "http://localhost:8000"
    run = f"?run={args[0]}" if args else ""
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2, color_scheme="light", locale="ko-KR")
        page.goto(f"{base}/{run}")
        page.wait_for_selector(".memo-title", timeout=30000)
        page.wait_for_timeout(800)  # 글꼴 로딩

        shot(page, "01_ai_memo_first_view")
        shot(page, "02_ai_memo_full", full=True)
        shot(page, "03_trace_and_memo", ".agent-grid")
        shot(page, "04_verification_stats", ".stats")
        shot(page, "05_peer_selection", "section[aria-label='Peer 선정']")

        page.locator(".ms-metric").first.click()
        page.wait_for_selector(".drawer .big", timeout=15000)
        page.wait_for_timeout(300)
        shot(page, "06_metric_provenance")
        page.locator(".drawer-close").click()

        page.locator(".ms-cite").first.click()
        page.wait_for_selector(".drawer", timeout=10000)
        page.wait_for_timeout(300)
        shot(page, "07_evidence_source")
        page.locator(".drawer-close").click()

        page.get_by_role("button", name="Peer 비교 대시보드").click()
        page.wait_for_selector(".page:not([hidden]) .kpis", timeout=30000)
        page.wait_for_timeout(500)
        shot(page, "08_dashboard", ".page:not([hidden])")

        page.locator(".page:not([hidden]) .ev-examples .example").nth(1).click()
        page.wait_for_selector(".page:not([hidden]) .ev", timeout=30000)
        page.wait_for_timeout(300)
        shot(page, "09_evidence_search", ".page:not([hidden]) section[aria-label='공시 근거 검색']")
        browser.close()


if __name__ == "__main__":
    main()
