from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import connect, init_database
from app.models import ReceiptLineItem, ReceiptParse
from app.services import local_ocr, recognition
from app.services.receipt_store import upsert_product_alias


def empty_parse() -> ReceiptParse:
    return ReceiptParse(
        store_name="Offline Market",
        purchased_at="2026-07-18",
        subtotal=0,
        tax=0,
        total=0,
        overall_confidence=0.9,
        image_quality_issue=None,
        items=[],
    )


def test_offline_is_default_needs_no_key_and_reserves_no_ai_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "offline.db"
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(database_path))
    monkeypatch.delenv("RECEIPT_SCAN_ENGINE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    expected = empty_parse()

    async def fake_local(_jpeg_bytes: bytes) -> ReceiptParse:
        return expected

    async def forbidden_openai(_jpeg_bytes: bytes) -> ReceiptParse:
        raise AssertionError("offline recognition must not enter the OpenAI path")

    monkeypatch.setattr(local_ocr, "parse_local_receipt_image", fake_local)
    monkeypatch.setattr(
        recognition, "parse_openai_receipt_image", forbidden_openai
    )

    assert asyncio.run(recognition.recognize_receipt_image(b"jpeg")) is expected
    assert recognition.recognition_provenance().model_dump() == {
        "mode": "live",
        "ai_called": False,
        "provider": "rapidocr",
        "model": "PP-OCRv6-small",
        "fixture_id": None,
    }

    init_database(database_path)
    with connect(database_path) as connection:
        usage = connection.execute("SELECT COUNT(*) FROM ai_call_usage").fetchone()[0]
    assert usage == 0


def test_openai_engine_dispatches_only_when_explicitly_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(tmp_path / "openai.db"))
    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-by-stub")
    get_settings.cache_clear()
    expected = empty_parse()
    calls = 0

    async def fake_openai(_jpeg_bytes: bytes) -> ReceiptParse:
        nonlocal calls
        calls += 1
        return expected

    async def forbidden_local(_jpeg_bytes: bytes) -> ReceiptParse:
        raise AssertionError("OpenAI selection must not enter the local OCR path")

    monkeypatch.setattr(recognition, "parse_openai_receipt_image", fake_openai)
    monkeypatch.setattr(local_ocr, "parse_local_receipt_image", forbidden_local)

    assert asyncio.run(recognition.recognize_receipt_image(b"jpeg")) is expected
    assert calls == 1
    assert recognition.recognition_provenance().provider == "openai"
    assert recognition.recognition_provenance().ai_called is True


def test_explicit_offline_override_cannot_enter_global_openai_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(tmp_path / "override.db"))
    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "openai")
    get_settings.cache_clear()
    expected = empty_parse()

    async def fake_local(_jpeg_bytes: bytes) -> ReceiptParse:
        return expected

    async def forbidden_openai(_jpeg_bytes: bytes) -> ReceiptParse:
        raise AssertionError("explicit offline override must not call OpenAI")

    monkeypatch.setattr(local_ocr, "parse_local_receipt_image", fake_local)
    monkeypatch.setattr(
        recognition, "parse_openai_receipt_image", forbidden_openai
    )

    recognized = asyncio.run(
        recognition.recognize_receipt_image(b"jpeg", engine="offline")
    )
    assert recognized is expected
    assert recognition.recognition_provenance(engine="offline").ai_called is False


def test_offline_recognizer_applies_exact_user_confirmed_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "aliases.db"
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(database_path))
    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "offline")
    get_settings.cache_clear()
    upsert_product_alias(
        "Mystery Sku",
        canonical_key="baby_spinach",
        display_name="Spinach",
        category="produce",
        is_perishable=True,
        merchant_name="Offline Market",
        database_path=database_path,
    )
    parsed = empty_parse().model_copy(
        update={
            "items": [
                ReceiptLineItem(
                    raw_text="MYSTERY SKU 42 9.99",
                    name="Mystery Sku",
                    canonical_key=None,
                    qty=1,
                    unit="each",
                    unit_price=2.99,
                    line_total=2.99,
                    category="unknown",
                    is_perishable=True,
                    storage=None,
                    eat_by_window=None,
                    confidence=0.6,
                    needs_review=True,
                )
            ]
        }
    )

    async def fake_local(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(local_ocr, "parse_local_receipt_image", fake_local)
    recognized = asyncio.run(recognition.recognize_receipt_image(b"jpeg"))

    assert recognized.items[0].canonical_key == "baby_spinach"
    assert recognized.items[0].name == "Spinach"
    assert recognized.items[0].category == "produce"
