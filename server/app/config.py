from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


SERVER_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(SERVER_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    demo_token: str
    admin_token: str
    cors_origins: tuple[str, ...]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cors_origins = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:8081,http://127.0.0.1:8081",
        ).split(",")
        if origin.strip()
    )
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6").strip() or "gpt-5.6",
        demo_token=os.getenv("DEMO_TOKEN", "").strip(),
        admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        cors_origins=cors_origins,
    )

