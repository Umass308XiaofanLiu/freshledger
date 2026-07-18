from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import local_ocr


def _box(left: float, top: float, right: float, bottom: float) -> list[list[float]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _mock_output() -> SimpleNamespace:
    return SimpleNamespace(
        boxes=[
            _box(100, 10, 300, 30),
            _box(20, 60, 230, 80),
            _box(400, 62, 460, 82),
            _box(250, 110, 350, 130),
            _box(400, 111, 470, 131),
        ],
        txts=(
            "LOCAL TEST MARKET",
            "BANAÑAS 2.31LB",
            "1.59",
            "TOTAL",
            "1.59",
        ),
        scores=(0.99, 0.96, 0.98, 0.99, 1.0),
    )


def test_async_local_ocr_uses_single_cached_engine_and_zero_cloud_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = 0
    inputs: list[bytes] = []

    class FakeEngine:
        def __call__(self, image: bytes) -> Any:
            inputs.append(image)
            return _mock_output()

    def create_engine() -> FakeEngine:
        nonlocal created
        created += 1
        return FakeEngine()

    local_ocr._ocr_engine.cache_clear()
    monkeypatch.setattr(local_ocr, "_create_ocr_engine", create_engine)
    try:
        first = asyncio.run(local_ocr.parse_local_receipt_image(b"first jpeg"))
        second = asyncio.run(local_ocr.parse_local_receipt_image(b"second jpeg"))
    finally:
        local_ocr._ocr_engine.cache_clear()

    assert created == 1
    assert inputs == [b"first jpeg", b"second jpeg"]
    assert first.store_name == "Local Test Market"
    assert first.total == 1.59
    assert len(first.items) == 1
    assert first.items[0].canonical_key == "banana"
    assert first.items[0].storage is None
    assert first.items[0].eat_by_window is None
    assert second == first


def test_legacy_rapidocr_tuple_output_preserves_boxes_and_scores() -> None:
    legacy = (
        [
            [_box(10, 20, 100, 40), "APPLE", 0.87],
            [_box(200, 21, 250, 41), "1.00", 0.93],
        ],
        [0.01, 0.02, 0.03],
    )
    tokens = local_ocr.tokens_from_rapidocr_output(legacy)
    lines = local_ocr.reconstruct_lines(tokens)

    assert len(tokens) == 2
    assert tokens[0].box[0] == (10.0, 20.0)
    assert tokens[0].confidence == 0.87
    assert len(lines) == 1
    assert lines[0].text == "APPLE 1.00"
    assert lines[0].confidence == 0.87


def test_empty_local_image_returns_validation_error() -> None:
    with pytest.raises(Exception) as exc_info:
        asyncio.run(local_ocr.parse_local_receipt_image(b""))

    assert getattr(exc_info.value, "status_code", None) == 422
    assert getattr(exc_info.value, "code", None) == "INVALID_IMAGE"
