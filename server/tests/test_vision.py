from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.models import ReceiptParse
from app.services import vision


@pytest.fixture(autouse=True)
def clear_cached_openai_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setenv("OPENAI_DAILY_CALL_LIMIT", "20")
    get_settings.cache_clear()
    vision._openai_client.cache_clear()
    yield
    get_settings.cache_clear()
    vision._openai_client.cache_clear()


def test_vision_uses_one_strict_gpt56_image_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6")
    get_settings.cache_clear()

    expected = ReceiptParse(
        store_name="Test Market",
        purchased_at="2026-07-17",
        subtotal=0,
        tax=0,
        total=0,
        overall_confidence=1,
        image_quality_issue=None,
        items=[],
    )
    calls: list[dict[str, Any]] = []

    class FakeResponses:
        async def parse(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(output_parsed=expected)

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.responses = FakeResponses()

    monkeypatch.setattr(vision, "AsyncOpenAI", FakeClient)
    result = asyncio.run(vision.parse_receipt_image(b"jpeg bytes"))

    assert result is expected
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == "gpt-5.6"
    assert call["text_format"] is ReceiptParse
    assert call["reasoning"] == {"effort": "low"}
    assert call["max_output_tokens"] == 4096
    image_input = call["input"][0]["content"][1]
    assert image_input["type"] == "input_image"
    assert image_input["detail"] == "high"
    assert image_input["image_url"].startswith("data:image/jpeg;base64,")

    get_settings.cache_clear()


def test_vision_maps_openai_5xx_to_ai_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "status-test-key")
    get_settings.cache_clear()

    class FailingResponses:
        async def parse(self, **_kwargs: Any) -> None:
            request = httpx.Request("POST", "https://api.openai.com/v1/responses")
            response = httpx.Response(503, request=request)
            raise vision.APIStatusError("service unavailable", response=response, body=None)

    class FailingClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.responses = FailingResponses()

    monkeypatch.setattr(vision, "AsyncOpenAI", FailingClient)
    with pytest.raises(Exception) as exc_info:
        asyncio.run(vision.parse_receipt_image(b"jpeg bytes"))

    assert getattr(exc_info.value, "status_code", None) == 504
    assert getattr(exc_info.value, "code", None) == "AI_TIMEOUT"
    get_settings.cache_clear()


def test_vision_daily_circuit_breaker_stops_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "budget-test-key")
    monkeypatch.setenv("OPENAI_DAILY_CALL_LIMIT", "1")
    get_settings.cache_clear()
    calls = 0
    expected = ReceiptParse(
        store_name="Budget Market",
        purchased_at="2026-07-18",
        subtotal=0,
        tax=0,
        total=0,
        overall_confidence=1,
        image_quality_issue=None,
        items=[],
    )

    class CountingResponses:
        async def parse(self, **_kwargs: Any) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(output_parsed=expected)

    class CountingClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.responses = CountingResponses()

    monkeypatch.setattr(vision, "AsyncOpenAI", CountingClient)
    assert asyncio.run(vision.parse_receipt_image(b"first")) is expected
    with pytest.raises(Exception) as exc_info:
        asyncio.run(vision.parse_receipt_image(b"second"))

    assert getattr(exc_info.value, "code", None) == "BUDGET_PAUSE"
    assert calls == 1
