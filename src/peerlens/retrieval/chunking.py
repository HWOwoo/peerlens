"""섹션 → 검색 단위 청크. 문단 경계를 지키고, 청크마다 원문 위치·출처 메타데이터를 단다."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

from peerlens.edgar.filings import SECTION_LABEL_KO, Block, FilingRef, Section

MAX_CHARS = 2200  # ≈ 450~550 토큰
MIN_CHARS = 300
_SUBHEAD_MAX = 160


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    ticker: str
    cik: int
    company: str
    form: str
    accn: str
    filed: str
    report_date: str
    section: str
    section_label: str
    item: str
    subheading: str
    ordinal: int
    text: str
    source_url: str
    anchor_url: str  # 원문 페이지에서 해당 문단으로 스크롤·강조 (Text Fragment)
    retrieved_at: str
    text_hash: str

    @property
    def embed_text(self) -> str:
        """임베딩·리랭크용 텍스트: 맥락 머리말 + 본문 (머리말이 회사·섹션 구분을 돕는다)."""
        head = f"{self.company} ({self.ticker}) {self.form} FY{self.report_date[:4]} — {self.section_label}"
        if self.subheading:
            head += f" — {self.subheading}"
        return f"{head}\n{self.text}"

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


_BULLET = ("•", "●", "◦", "▪", "-", "–", "—", "(")
_PAGE_HEADER = re.compile(r"^table\s*of\s*conten\s*t\s*s$", re.IGNORECASE)


def _is_page_header(b: Block) -> bool:
    return bool(_PAGE_HEADER.match(b.text))


def _is_subheading(b: Block) -> bool:
    t = b.text
    return (
        not b.in_table
        and len(t) <= _SUBHEAD_MAX
        and not t.endswith((".", ":", ";", ","))
        and not t.startswith(_BULLET)
        and t[:1].isupper()
        and len(t.split()) >= 2
    )


def _is_numeric_row(b: Block) -> bool:
    """표 행은 문장형(마침표로 끝나는 긴 글)만 남긴다. 재무표·표 머리글은 제외 — 수치는 XBRL에서만 (원칙 1).
    문장형 표 행은 일부 공시가 글머리표 목록을 표로 배치한 경우다."""
    if not b.in_table:
        return False
    sentence_like = len(b.text) >= 60 and len(b.text.split()) >= 8 and b.text.rstrip().endswith((".", ";", ":"))
    letters = sum(ch.isalpha() for ch in b.text)
    return not sentence_like or letters < len(b.text) * 0.5


def anchor_url(source_url: str, text: str, words: int = 8) -> str:
    """브라우저 Text Fragment(#:~:text=)로 원문 해당 위치에 바로 이동. 문장 앞 몇 단어만 쓴다."""
    first = " ".join(re.sub(r"[^\w\s'’,\-]", " ", text).split()[:words])
    return f"{source_url}#:~:text={quote(first, safe='')}" if first else source_url


def chunk_section(ref: FilingRef, section: Section, retrieved_at: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_head = ""
    subheading = ""

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        buf = []
        if len(text) < 80:
            return
        # 직전 청크가 너무 짧으면 합친다
        if chunks and len(chunks[-1].text) < MIN_CHARS and chunks[-1].subheading == buf_head:
            prev = chunks.pop()
            text = prev.text + "\n" + text
        ordinal = len(chunks)
        cid = f"{ref.ticker}:{ref.accn}:{section.key}:{ordinal:04d}"
        chunks.append(Chunk(
            chunk_id=cid,
            ticker=ref.ticker,
            cik=ref.cik,
            company=ref.company,
            form=ref.form,
            accn=ref.accn,
            filed=ref.filed,
            report_date=ref.report_date,
            section=section.key,
            section_label=SECTION_LABEL_KO[section.key],
            item=section.item,
            subheading=buf_head,
            ordinal=ordinal,
            text=text,
            source_url=ref.url,
            anchor_url=anchor_url(ref.url, text),
            retrieved_at=retrieved_at,
            text_hash=hashlib.sha256(f"{buf_head}\n{text}".encode()).hexdigest()[:16],
        ))

    for b in section.blocks:
        if _is_numeric_row(b) or _is_page_header(b):
            continue
        if _is_subheading(b):
            if sum(len(x) for x in buf) >= MIN_CHARS:
                flush()
            subheading = b.text
            if not buf:
                buf_head = subheading
            continue
        if not buf:
            buf_head = subheading
        if buf and sum(len(x) for x in buf) + len(b.text) > MAX_CHARS:
            flush()
            buf_head = subheading
        # 아주 긴 문단은 문장 단위로 쪼갠다
        if len(b.text) > MAX_CHARS:
            for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", b.text):
                if buf and sum(len(x) for x in buf) + len(sent) > MAX_CHARS:
                    flush()
                    buf_head = subheading
                buf.append(sent)
        else:
            buf.append(b.text)
    flush()
    return chunks
