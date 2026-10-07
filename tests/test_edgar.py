import time

import pytest

from peerlens.config import Settings
from peerlens.edgar.client import EdgarClient, _RateLimiter, filing_index_url


def test_user_agent_must_contain_name_and_email(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "anonymous")
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_rps_capped_at_sec_limit(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("SEC_MAX_RPS", "50")
    assert Settings.from_env().sec_max_rps == 10.0


def test_rate_limiter_spacing():
    limiter = _RateLimiter(max_rps=20)
    t0 = time.monotonic()
    for _ in range(5):
        limiter.wait()
    # Windows 타이머 해상도(~15.6ms)만큼 여유를 둔다
    assert time.monotonic() - t0 >= 4 / 20 - 0.035


def test_offline_cache_miss_raises(tmp_path):
    client = EdgarClient(Settings("Test User test@example.com", 8, tmp_path), offline=True)
    with pytest.raises(FileNotFoundError):
        client.company_facts(1045810)


def test_filing_index_url():
    assert filing_index_url(1045810, "0001045810-25-000023") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/0001045810-25-000023-index.htm"
    )


@pytest.mark.live
def test_live_nvda_fy2025_revenue():
    from peerlens.metrics.pipeline import load_facts

    with EdgarClient() as c:
        facts = load_facts(c, "NVDA")
    rev = next(f for f in facts if f.concept == "revenue" and f.fiscal_year == 2025)
    assert rev.value == 130_497_000_000
    assert rev.period_end == "2025-01-26"
