"""연간 공시(10-K·20-F) 원문 수집 → 문단 블록 → 표준 섹션 분할.

섹션 제목("Item 1A. Risk Factors")은 앞쪽 목차와 본문에 모두 나온다. 제목이 나올 때마다 다음 제목까지의
길이를 재서, 같은 Item 중 가장 긴 구간을 본문으로 본다 (목차 항목은 길이가 매우 짧다).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

from peerlens.edgar.client import EdgarClient

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}"

# 표준 섹션 ← 양식별 Item 번호
SECTION_MAP: dict[str, dict[str, str]] = {
    "10-K": {"1": "business", "1A": "risk_factors", "7": "mdna", "7A": "market_risk"},
    "20-F": {"4": "business", "3": "risk_factors", "5": "mdna", "11": "market_risk"},
}
SECTION_LABEL_KO = {
    "business": "사업 개요",
    "risk_factors": "위험 요인",
    "mdna": "경영진 분석(MD&A)",
    "market_risk": "시장 위험",
}

_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "body", "ul", "ol"}
_HEADING_RE = re.compile(r"^\s*item\s+(\d{1,2}[a-d]?)\s*[.:\-—–]?\s*(.{0,200})$", re.IGNORECASE)
_NOISE_RE = re.compile(r"^(table of contents|\d{1,3}|page \d+|[ivx]{1,5})$", re.IGNORECASE)


@dataclass(frozen=True)
class FilingRef:
    ticker: str
    cik: int
    company: str
    form: str
    accn: str
    filed: str
    report_date: str
    primary_doc: str

    @property
    def url(self) -> str:
        return ARCHIVE_DOC_URL.format(cik=self.cik, accn_nodash=self.accn.replace("-", ""), doc=self.primary_doc)


@dataclass
class Block:
    text: str
    in_table: bool = False


@dataclass
class Section:
    key: str  # 표준 섹션 키 (business, risk_factors …)
    item: str  # 원 양식의 Item 번호 (1A 등)
    title: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def n_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)


def latest_annual_filing(client: EdgarClient, ticker: str, forms: tuple[str, ...] = ("10-K", "20-F")) -> FilingRef:
    cik, _ = client.resolve_ticker(ticker)
    sub = client.submissions(cik).data
    rec = sub["filings"]["recent"]
    for i, form in enumerate(rec["form"]):
        if form in forms:
            return FilingRef(
                ticker=ticker.upper(),
                cik=cik,
                company=sub.get("name", ""),
                form=form,
                accn=rec["accessionNumber"][i],
                filed=rec["filingDate"][i],
                report_date=rec["reportDate"][i],
                primary_doc=rec["primaryDocument"][i],
            )
    raise LookupError(f"{ticker}: no {'/'.join(forms)} in recent filings")


def fetch_filing_html(client: EdgarClient, ref: FilingRef) -> tuple[str, str]:
    """(html, retrieved_at)"""
    resp = client.get_text(ref.url, f"filings/{ref.cik}/{ref.accn}/{ref.primary_doc}")
    return resp.data, resp.retrieved_at


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def html_to_blocks(html: str) -> list[Block]:
    """하위에 블록 요소가 없는 요소를 한 문단으로 본다. 표는 행 단위 블록(in_table)으로."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    # 인라인 XBRL 숨김 헤더(ix:header)는 화면에 보이지 않는 데이터라 제거
    for t in soup.find_all(re.compile(r"^ix:header$")):
        t.decompose()
    for t in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        t.decompose()

    blocks: list[Block] = []

    def walk(node: Tag, in_table: bool) -> None:
        if node.name == "tr":
            cells = [_norm(td.get_text(" ")) for td in node.find_all(["td", "th"])]
            text = _norm(" ".join(c for c in cells if c))
            if text:
                blocks.append(Block(text, in_table=True))
            return
        has_block_child = any(isinstance(ch, Tag) and ch.name in _BLOCK_TAGS for ch in node.children)
        if not has_block_child:
            text = _norm(node.get_text(" "))
            if text:
                blocks.append(Block(text, in_table=in_table))
            return
        buf: list[str] = []  # 블록 사이에 낀 인라인 텍스트
        for ch in node.children:
            if isinstance(ch, NavigableString):
                buf.append(str(ch))
            elif isinstance(ch, Tag) and ch.name in _BLOCK_TAGS:
                if _norm("".join(buf)):
                    blocks.append(Block(_norm("".join(buf)), in_table=in_table))
                buf = []
                walk(ch, in_table or ch.name == "table")
            elif isinstance(ch, Tag):
                buf.append(ch.get_text(" "))
        if _norm("".join(buf)):
            blocks.append(Block(_norm("".join(buf)), in_table=in_table))

    walk(soup.body or soup, False)
    return reflow_lines([b for b in blocks if not _NOISE_RE.match(b.text)])


_TERMINAL = (".", "?", "!", ":", ";", "”", '"')


def reflow_lines(blocks: list[Block]) -> list[Block]:
    """PDF에서 변환된 공시(TSMC 20-F 등)는 한 줄이 한 블록이다. 이런 문서면 줄을 이어 문단으로 복원한다.

    판정: 본문 블록 중 '거의 줄 폭만큼 길고 문장부호로 끝나지 않는' 블록이 30% 이상.
    복원: 직전 줄이 줄 폭의 80% 이상이면(=줄이 꽉 차서 넘어간 것) 다음 줄을 이어 붙인다.
    """
    lens = sorted(len(b.text) for b in blocks if not b.in_table)
    if len(lens) < 50:
        return blocks
    width = lens[int(len(lens) * 0.75)]
    body = [b for b in blocks if not b.in_table]
    wrapped = sum(1 for b in body if 0.8 * width <= len(b.text) <= 1.2 * width and not b.text.endswith(_TERMINAL))
    if wrapped < 0.3 * len(body):
        return blocks

    out: list[Block] = []
    cur: list[str] = []
    last_len = 0

    def flush() -> None:
        nonlocal cur
        if cur:
            out.append(Block(_join(cur)))
        cur = []

    for b in blocks:
        if b.in_table:
            flush()
            out.append(b)
            continue
        if cur and last_len >= 0.8 * width and not _HEADING_RE.match(b.text):
            cur.append(b.text)
        else:
            flush()
            cur = [b.text]
        last_len = len(b.text)
    flush()
    return out


def _join(lines: list[str]) -> str:
    text = lines[0]
    for ln in lines[1:]:
        if text.endswith("-") and ln[:1].islower():  # 줄 끝 하이픈으로 나뉜 단어
            text = text[:-1] + ln
        else:
            text += " " + ln
    return text


def split_sections(blocks: list[Block], form: str) -> dict[str, Section]:
    base_form = "20-F" if form.startswith("20-F") else "10-K"
    mapping = SECTION_MAP[base_form]

    heads: list[tuple[int, str, str]] = []  # (block index, item, title)
    for i, b in enumerate(blocks):
        if len(b.text) > 220:
            continue
        m = _HEADING_RE.match(b.text)
        if m:
            heads.append((i, m.group(1).upper(), m.group(2).strip(" .")))

    # 각 제목에서 다음 제목까지의 글자 수
    spans: list[tuple[int, int, str, str, int]] = []
    for n, (i, item, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(blocks)
        size = sum(len(b.text) for b in blocks[i + 1 : end])
        spans.append((i, end, item, title, size))

    sections: dict[str, Section] = {}
    for item, key in mapping.items():
        cands = [s for s in spans if s[2] == item]
        if not cands:
            continue
        i, end, _, title, size = max(cands, key=lambda s: s[4])
        if size < 500:  # 본문이 없으면 (참조만 있는 경우) 건너뜀
            continue
        if not title:  # 제목이 다음 블록에 따로 있는 경우
            title = blocks[i + 1].text if i + 1 < len(blocks) and len(blocks[i + 1].text) < 150 else ""
        sections[key] = Section(key=key, item=item, title=title, blocks=blocks[i + 1 : end])

    if len(sections) < len(mapping):
        for key, sec in _split_by_toc(blocks).items():
            sections.setdefault(key, sec)
    return sections


# ---- 목차 기반 보조 분할 (INTC처럼 'Item' 제목 대신 연차보고서 형식인 경우) ----

_TOC_ENTRY_RE = re.compile(r"^(?P<title>[A-Za-z][A-Za-z0-9&'’ ,()\-]{2,90}?)(?:\s+(?P<page>\d{1,3}))?$")
_TITLE_PATTERNS: dict[str, list[str]] = {
    "business": [r"business", r"our business(es)?", r"overview"],
    "risk_factors": [r"risk factors"],
    "mdna": [r"(md&a )?management's discussion and analysis( of financial condition and results of operations)?"],
    "market_risk": [r"quantitative and qualitative disclosures? about market risk"],
}


def _key(s: str) -> str:
    return _norm(s).lower().replace("’", "'")


def _toc_entries(blocks: list[Block]) -> tuple[list[tuple[str, bool]], int]:
    """문서 앞쪽 목차 → ([(제목, 쪽번호 有無)], 목차 끝 인덱스). 쪽번호 없는 항목은 상위 그룹 제목."""
    limit = max(200, len(blocks) // 6)
    start = next((i for i, b in enumerate(blocks[:limit]) if _TOC_ENTRY_RE.match(b.text) and _TOC_ENTRY_RE.match(b.text)["page"]), None)
    if start is None:
        return [], 0
    entries: list[tuple[str, bool]] = []
    i = start
    while i < len(blocks) and len(blocks[i].text) <= 100:
        m = _TOC_ENTRY_RE.match(blocks[i].text)
        if m:
            entries.append((_key(m["title"]), bool(m["page"])))
        i += 1
    return entries, i


def _split_by_toc(blocks: list[Block]) -> dict[str, Section]:
    entries, toc_end = _toc_entries(blocks)
    if not entries:
        return {}
    titles = {t for t, _ in entries}
    groups = {t for t, has_page in entries if not has_page}

    def body_index(title: str, after: int) -> int | None:
        return next((i for i in range(after, len(blocks)) if _key(blocks[i].text) == title), None)

    starts: dict[str, tuple[int, str]] = {}
    for key, pats in _TITLE_PATTERNS.items():
        for pat in pats:
            title = next((t for t, _ in entries if re.fullmatch(pat, t)), None)
            idx = body_index(title, toc_end) if title else None
            if idx is not None:
                starts[key] = (idx, title)
                break

    sections: dict[str, Section] = {}
    for key, (idx, title) in starts.items():
        bounds = [i for k, (i, _) in starts.items() if k != key and i > idx]
        bounds += [i for g in groups if g != title and (i := body_index(g, idx + 1)) is not None]
        if title not in groups:  # 쪽 단위 항목이면 목차상 다음 항목에서 끝
            pos = [t for t, _ in entries].index(title)
            if pos + 1 < len(entries) and (i := body_index(entries[pos + 1][0], idx + 1)) is not None:
                bounds.append(i)
        end = min(bounds, default=len(blocks))
        body = [
            b for b in blocks[idx + 1 : end]
            if not ((m := _TOC_ENTRY_RE.match(b.text)) and m["page"] and _key(m["title"]) in titles)  # 페이지 머리말
        ]
        if sum(len(b.text) for b in body) >= 500:
            sections[key] = Section(key=key, item="", title=blocks[idx].text, blocks=body)
    return sections
