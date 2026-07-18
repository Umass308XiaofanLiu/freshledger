from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def disable_startup_seed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test isolated unless it explicitly exercises startup seeding."""

    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
