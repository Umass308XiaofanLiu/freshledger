from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import get_settings
from app.models import ReceiptParse
from app.services import vision


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

