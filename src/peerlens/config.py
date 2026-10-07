"""환경 설정. 비밀값은 .env에서만 읽는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    sec_max_rps: float
    cache_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        ua = os.getenv("SEC_USER_AGENT", "").strip()
        if "@" not in ua or " " not in ua:
            raise RuntimeError(
                "SEC_USER_AGENT must contain a name and an email (e.g. 'Jane Doe jane@example.com'). "
                "Copy .env.example to .env and fill it in."
            )
        rps = min(float(os.getenv("SEC_MAX_RPS", "8")), 10.0)
        cache = Path(os.getenv("PEERLENS_CACHE_DIR", "data/cache"))
        if not cache.is_absolute():
            cache = PROJECT_ROOT / cache
        return cls(sec_user_agent=ua, sec_max_rps=rps, cache_dir=cache)
