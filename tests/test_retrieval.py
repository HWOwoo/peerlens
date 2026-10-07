import hashlib

import pytest
from qdrant_client import QdrantClient

from peerlens.edgar.filings import Block, FilingRef, html_to_blocks, reflow_lines, split_sections
from peerlens.retrieval import bm25
from peerlens.retrieval.chunking import chunk_section
from peerlens.retrieval.index import FilingIndex, rrf
from peerlens.retrieval.providers import RerankHit

REF = FilingRef("TST", 1, "Test Corp", "10-K", "0000000001-26-000001", "2026-02-01", "2025-12-31", "tst-20251231.htm")

LOREM = "Our business depends on a limited number of customers and suppliers in several regions. " * 12


def _tenk_html() -> str:
    toc = "".join(f"<tr><td>Item {i}.</td><td>{t}</td><td>{p}</td></tr>" for i, t, p in [
        ("1", "Business", 3), ("1A", "Risk Factors", 10), ("7", "Management's Discussion and Analysis", 30)])
    return f"""<html><body>
    <div style="display:none"><ix:header>hidden xbrl facts</ix:header></div>
    <table>{toc}</table>
    <p>Item 1. Business</p><p>Our Company</p><p>{LOREM}</p>
    <p>Item 1A. Risk Factors</p><p>Risks Related to Customers</p><p>Customer concentration: {LOREM}</p>
    <p>• a bullet that is not a heading</p>
    <table><tr><td>Revenue</td><td>$ 1,234</td><td>$ 999</td></tr></table>
    <p>Item 7. Management's Discussion and Analysis</p><p>{LOREM}</p>
    <p>Item 8. Financial Statements</p><p>{LOREM}</p>
    </body></html>"""


def test_split_sections_skips_toc_and_hidden_xbrl():
    blocks = html_to_blocks(_tenk_html())
    assert not any("hidden xbrl" in b.text for b in blocks)
    secs = split_sections(blocks, "10-K")
    assert set(secs) == {"business", "risk_factors", "mdna"}
    assert secs["risk_factors"].item == "1A"
    assert secs["risk_factors"].blocks[0].text == "Risks Related to Customers"
    assert "Financial Statements" not in " ".join(b.text for b in secs["mdna"].blocks)


def test_toc_fallback_for_annual_report_style():
    toc = ["Overview 3", "Our Business 6", "Management's Discussion and Analysis", "Operating Results 21",
           "Risk Factors 37", "Other Key Information", "Cybersecurity 54"]
    body = (["Our Business", LOREM, "Management's Discussion and Analysis", "Operating Results", LOREM,
             "Risk Factors", LOREM, "Risk Factors 38", LOREM, "Other Key Information", LOREM])
    blocks = [Block("Annual Report")] + [Block(t) for t in toc] + [Block(LOREM)] + [Block(t) for t in body]
    secs = split_sections(blocks, "10-K")
    assert set(secs) >= {"business", "risk_factors", "mdna"}
    rf = " ".join(b.text for b in secs["risk_factors"].blocks)
    assert "Risk Factors 38" not in rf  # 페이지 머리말 제거
    assert "Other Key Information" not in rf
    md = " ".join(b.text for b in secs["mdna"].blocks)
    assert LOREM.strip() in md and "Risk Factors" not in md  # MD&A는 위험요인 시작에서 끝


def test_reflow_joins_pdf_style_lines():
    line = "x" * 100
    blocks = [Block(f"{line} word{i} and more text to fill the line here ok") for i in range(60)]
    blocks.insert(30, Block("Short heading line"))
    blocks.append(Block("End of paragraph."))
    out = reflow_lines(blocks)
    assert len(out) < 10
    assert any(b.text == "Short heading line" for b in out) or any("Short heading line" in b.text for b in out)


def test_reflow_leaves_normal_documents_alone():
    blocks = [Block(f"Sentence number {i} ends properly with a period.") for i in range(80)]
    assert reflow_lines(blocks) == blocks


def test_chunks_carry_source_metadata_and_skip_numeric_rows():
    secs = split_sections(html_to_blocks(_tenk_html()), "10-K")
    chunks = chunk_section(REF, secs["risk_factors"], "2026-10-07T00:00:00+00:00")
    assert chunks and all(c.section == "risk_factors" and c.accn == REF.accn for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert "$ 1,234" not in joined
    assert chunks[0].subheading == "Risks Related to Customers"
    assert chunks[0].anchor_url.startswith(REF.url + "#:~:text=Customer%20concentration")
    assert "Test Corp (TST) 10-K" in chunks[0].embed_text


def test_bm25_vectors():
    idx, val = bm25.doc_vector("customer customer concentration risk")
    assert len(idx) == 3 and max(val) > min(val)  # 반복 단어 가중치↑ (포화)
    q_idx, q_val = bm25.query_vector("the customer")
    assert len(q_idx) == 1 and q_val == [1.0]  # 불용어 제거


def test_rrf_rewards_agreement():
    fused = dict(rrf([["a", "b", "c"], ["b", "c", "a"]]))
    assert fused["b"] > fused["c"]
    assert list(dict(rrf([["x"], ["y", "x"]])))[0] == "x"


class FakeEmbedder:
    """단어 해시 기반 결정적 임베딩 — 같은 단어가 많을수록 코사인 유사도↑."""
    model, dim = "fake", 64

    def embed(self, texts, input_type):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in bm25.tokenize(t):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            out.append(v if any(v) else [1.0] + [0.0] * (self.dim - 1))
        return out


class FakeReranker:
    model = "fake"

    def rerank(self, query, documents, top_n):
        q = set(bm25.tokenize(query))
        scored = sorted(range(len(documents)), key=lambda i: -len(q & set(bm25.tokenize(documents[i]))))
        return [RerankHit(i, 1.0 / (r + 1)) for r, i in enumerate(scored[:top_n])]


@pytest.fixture
def index():
    return FilingIndex(FakeEmbedder(), FakeReranker(), client=QdrantClient(":memory:"))


def _chunks():
    secs = split_sections(html_to_blocks(_tenk_html()), "10-K")
    return [c for s in secs.values() for c in chunk_section(REF, s, "2026-10-07T00:00:00+00:00")]


def test_index_is_idempotent(index):
    chunks = _chunks()
    assert index.index_chunks(chunks) == (len(chunks), 0)
    assert index.index_chunks(chunks) == (0, len(chunks))
    assert index.count("TST") == len(chunks)


def test_search_filters_and_reports_stage_ranks(index):
    index.index_chunks(_chunks())
    hits = index.search("customer concentration", sections=["risk_factors"], k=2)
    assert hits and all(h.chunk["section"] == "risk_factors" for h in hits)
    assert hits[0].rank == 1 and hits[0].fused_rank >= 1
    assert index.search("anything", tickers=["NONE"]) == []
