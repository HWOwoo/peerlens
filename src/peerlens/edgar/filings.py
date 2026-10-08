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
    # 분기보고서: Part I Item 2 = MD&A, Item 3 = 시장위험, Part II Item 1A = 위험요인 변경 사항
    # (Part II의 Item 2는 자사주 매입 등 짧은 항목이라 '가장 긴 구간' 규칙으로 Part I MD&A가 선택된다)
    "10-Q": {"2": "mdna", "1A": "risk_factors", "3": "market_risk"},
}
SECTION_LABEL_KO = {
    "business": "사업 개요",
    "risk_factors": "위험 요인",
    "mdna": "경영진 분석(MD&A)",
    "market_risk": "시장 위험",
}

_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "body", "ul", "ol"}
_HEADING_RE = re.compile(r"^\s*item\s+(\d{1,2}[a-d]?)\s*[.:\-—–]?\s*(.{0,200})$", re.IGNORECASE)
# 페이지 머리말: "PART II", "PART II, III", "Item 7", "Item 9B, 9C, 10, 11"
_RUNNING_HEADER_RE = re.compile(
    r"^(part\s+[ivx]+(\s*,\s*[ivx]+)*|item\s+\d{1,2}[a-d]?(\s*,\s*\d{1,2}[a-d]?)*)$", re.IGNORECASE
)
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


def latest_quarterly_filing(client: EdgarClient, ticker: str) -> FilingRef | None:
    """최신 10-Q. 최신 연간 공시보다 나중에 낸 것만 (연간 공시 직후엔 없음)."""
    try:
        annual = latest_annual_filing(client, ticker)
        q = latest_annual_filing(client, ticker, forms=("10-Q",))
    except LookupError:
        return None
    return q if q.filed > annual.filed else None


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
    base_form = "20-F" if form.startswith("20-F") else "10-Q" if form.startswith("10-Q") else "10-K"
    mapping = SECTION_MAP[base_form]

    heads: list[tuple[int, str, str]] = []  # (block index, item, title)
    for i, b in enumerate(blocks):
        if len(b.text) > 220:
            continue
        m = _HEADING_RE.match(b.text)
        if m:
            heads.append((i, m.group(1).upper(), m.group(2).strip(" .")))

    # 각 제목에서 '번호가 다른' 다음 제목까지의 글자 수.
    # MSFT처럼 페이지마다 "PART I / Item 1" 머리말이 반복되는 문서에서 섹션이 페이지 단위로 잘리지 않게 한다.
    spans: list[tuple[int, int, str, str, int]] = []
    for n, (i, item, title) in enumerate(heads):
        end = next((h[0] for h in heads[n + 1 :] if h[1] != item), len(blocks))
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
        body = [b for b in blocks[i + 1 : end] if not _RUNNING_HEADER_RE.match(b.text)]
        sections[key] = Section(key=key, item=item, title=title, blocks=body)

    if len(sections) < len(mapping):
        for key, sec in _split_by_toc(blocks).items():
            if key in mapping.values():  # 10-Q에는 '사업 개요'가 없다
                sections.setdefault(key, sec)
    if base_form == "20-F" and len(sections) < len(mapping):
        for key, sec in _split_by_pages(blocks).items():
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
    """제목 비교용 정규화. "MD&A Management's…", "Management's… (MD&A)" 같은 표기 차이를 없앤다."""
    k = _norm(s).lower().replace("’", "'")
    k = re.sub(r"^md&a\s+|\s*\(md&a\)$", "", k)
    return k


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


# ---- 쪽 번호 기반 분할 (ASML처럼 연차보고서 PDF를 20-F로 낸 경우) ----
# 본문에 Item 제목이 없고, 끝의 '20-F 대조표'(Item → 보고서 위치·쪽)로 해당 내용을 찾게 되어 있다.
# 쪽 머리말("… Annual Report 2025 66")로 쪽 경계를 잡고, 대조표의 쪽 번호로 섹션을 모은다.

_PAGE_MARK_RE = re.compile(r"^(.{10,120}?)\s+(\d{1,3})$")
_XREF_HEAD_RE = re.compile(r"Item\s+Form 20-F caption\s+Location in this document\s+Page", re.I)
# 대조표에서 Item 번호 + 표준 제목 (다음 Item 위치를 찾는 데만 쓴다)
_XREF_ITEMS = [
    ("1", r"Identity of Directors"), ("2", r"Offer Statistics"), ("3", r"Key Information"),
    ("4", r"Information on the Company"), ("4A", r"Unresolved Staff Comments"),
    ("5", r"Operating and Financial Review"), ("6", r"Directors, Senior Management and Employees"),
    ("7", r"Major Shareholders"), ("8", r"Financial Information"), ("9", r"The Offer and Listing"),
    ("10", r"Additional Information"), ("11", r"Quantitative and Qualitative Disclosures"),
    ("12", r"Description of Securities"), ("13", r"Defaults, Dividend"),
]
# 표준 섹션 ← (Item, [(포함할 소항목 시작, 끝)]) — None이면 Item 전체. 앞쪽이 우선 (같은 쪽은 한 섹션에만)
# MD&A는 영업실적(A)·추세(D)만: 유동성(B)은 재무제표 주석 쪽이 대부분이라 제외
_XREF_SECTIONS: list[tuple[str, str, list[tuple[str | None, str | None]]]] = [
    ("risk_factors", "3", [(r"D\. Risk Factors", None)]),
    ("market_risk", "11", [(None, None)]),
    ("mdna", "5", [(r"A\. Operating Results", r"B\. Liquidity"), (r"D\. Trend Information", r"E\. Critical")]),
    ("business", "4", [(r"B\. Business Overview", r"C\. Organizational Structure")]),
]
_MAX_PAGES = 12  # 대조표의 한 위치에서 이어 읽는 최대 쪽 수
# PDF 변환으로 갈라진 단어 "R ead" → "Read" (앞 단어가 대문자로 끝나면 약어 "ASM L"이라 건드리지 않음)
_SPLIT_WORD_RE = re.compile(r"(?<![A-Za-z])(?<![A-Z] )([B-HJ-Z]) ([a-z]{2,})\b")


def _pages(blocks: list[Block]) -> tuple[list[tuple[int, int]], set[str]]:
    """[(쪽 번호, 머리말 블록 인덱스)], 머리말 바로 뒤에 반복되는 메뉴 줄."""
    from collections import Counter

    marks = [(i, m) for i, b in enumerate(blocks) if len(b.text) <= 140 and (m := _PAGE_MARK_RE.match(b.text))]
    common = Counter(m.group(1) for _, m in marks).most_common(1)
    if not common or common[0][1] < 20:
        return [], set()
    prefix = common[0][0]
    pages = [(int(m.group(2)), i) for i, m in marks if m.group(1) == prefix]
    nav = Counter(blocks[i + 1].text for _, i in pages if i + 1 < len(blocks))
    return pages, {t for t, c in nav.items() if c >= 3}


def _page_numbers(seg: str) -> list[int]:
    seg = re.sub(r"\bNote \d+[A-Z]?\b|\bExhibit [\d.]+", " ", seg)  # 주석·첨부 번호는 쪽 번호가 아님
    return [int(p) for p in re.findall(r"(?<=\s)(\d{1,3})(?=\s|$)", f" {seg} ")]


def _xref_pages(blocks: list[Block]) -> tuple[dict[str, list[int]], set[int]]:
    """20-F 대조표 → ({표준 섹션: [시작 쪽]}, 대조표에 나온 모든 쪽)"""
    text = _XREF_HEAD_RE.sub(" ", " ".join(b.text for b in blocks if _XREF_HEAD_RE.search(b.text)))
    if not text.strip():
        return {}, set()
    starts = sorted(
        (m.start(), m.end(), item) for item, cap in _XREF_ITEMS
        if (m := re.search(rf"(?<![\w.]){item}\s+{cap}", text, re.I))
    )
    # Item 번호·제목은 빼고 위치·쪽 부분만
    segs = {item: text[e : (starts[n + 1][0] if n + 1 < len(starts) else len(text))] for n, (_, e, item) in enumerate(starts)}
    out: dict[str, list[int]] = {}
    for key, item, ranges in _XREF_SECTIONS:
        seg = segs.get(item, "")
        parts = []
        for a, b in ranges:
            m = re.search(a, seg) if a else None
            if a and not m:
                continue
            part = seg[m.start():] if m else seg
            if b and (mb := re.search(b, part)):
                part = part[: mb.start()]
            parts.append(part)
        out[key] = _page_numbers(" ".join(parts))
    return out, set(_page_numbers(" ".join(segs.values())))


def _title_key(blocks: list[Block]) -> str:
    t = blocks[0].text if blocks else ""
    return re.sub(r"\s+", "", re.sub(r"\(\s*continued\s*\).*$", "", t)).lower()[:40]


def _split_by_pages(blocks: list[Block]) -> dict[str, Section]:
    pages, nav = _pages(blocks)
    refs, all_refs = _xref_pages(blocks)
    if not pages or not refs:
        return {}
    start_of = {p: i for p, i in pages}
    order = [p for p, _ in pages]

    def body(p: int) -> list[Block]:
        i = start_of[p]
        nxt = next((j for _, j in pages if j > i), len(blocks))
        return [b for b in blocks[i + 1 : nxt] if b.text not in nav]

    bodies = {p: body(p) for p in order}
    covers = {p for p in order if sum(len(b.text) for b in bodies[p]) < 300}  # 장 표지

    def run(p: int, used: set[int]) -> list[int]:
        """p쪽부터 이어 읽기. 장 표지, 대조표의 다른 위치(제목이 바뀐 경우), 최대 쪽 수에서 멈춘다."""
        out = [p]
        for q in order[order.index(p) + 1 :]:
            if q in used or q in covers or q - p >= _MAX_PAGES:
                break
            # 제목이 같으면 같은 글의 다음 쪽. 단 재무제표 주석은 모든 쪽 제목이 같아 대조표 쪽에서 끊는다
            title = _title_key(bodies[q])
            if q in all_refs and (title != _title_key(bodies[out[-1]]) or title.startswith("notesto")):
                break
            out.append(q)
        return out

    used: set[int] = set()
    sections: dict[str, Section] = {}
    for key, item, _ in _XREF_SECTIONS:
        chosen: list[int] = []
        for p in refs.get(key, []):
            if p in start_of and p not in used and p not in chosen:
                chosen += [q for q in run(p, used) if q not in chosen]
        used.update(chosen)
        blks = _rejoin([b for p in sorted(chosen) for b in bodies[p]])
        if sum(len(b.text) for b in blks) >= 500:
            title = re.sub(r"\s*\(\s*continued\s*\).*$", "", blks[0].text)[:120]
            sections[key] = Section(key=key, item=item, title=title, blocks=blks)
    return sections


def _rejoin(blocks: list[Block]) -> list[Block]:
    """PDF 줄 단위 블록 이어 붙이기: 앞 줄이 문장부호로 끝나지 않고 다음 줄이 소문자로 시작하면 같은 문단."""
    out: list[Block] = []
    for b in blocks:
        text = _SPLIT_WORD_RE.sub(r"\1\2", b.text)
        if out and not b.in_table and not out[-1].in_table and not out[-1].text.endswith(_TERMINAL) and text[:1].islower():
            out[-1] = Block(_join([out[-1].text, text]))
        else:
            out.append(Block(text, in_table=b.in_table))
    return out
