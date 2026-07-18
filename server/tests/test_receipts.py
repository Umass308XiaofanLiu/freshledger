from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.main import app
from app.models import EatByWindow, ReceiptLineItem, ReceiptParse, StoragePlan
from app.routers import receipts
from app.services.images import MAX_LONG_EDGE, MAX_SHORT_EDGE, prepare_receipt_image


@pytest.fixture(autouse=True)
def configured_demo_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "test-demo-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def make_jpeg(width: int = 600, height: int = 1000) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="JPEG")
    return output.getvalue()


def parsed_receipt() -> ReceiptParse:
    return ReceiptParse(
        store_name="Test Market",
        purchased_at="2026-07-17",
        subtotal=3.49,
        tax=0.0,
        total=3.49,
        overall_confidence=0.98,
        image_quality_issue=None,
        items=[
            ReceiptLineItem(
                raw_text="ORG BABY SPIN 5OZ",
                name="Organic Baby Spinach",
                canonical_key="spinach",
                qty=1,
                unit="each",
                unit_price=3.49,
                line_total=3.49,
                category="produce",
                is_perishable=True,
                storage=StoragePlan(method="fridge", temp_c=4, duration_days=5),
                eat_by_window=EatByWindow(start_days=0, end_days=5),
                confidence=0.98,
                needs_review=False,
            )
        ],
    )


def test_health_does_not_require_auth(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_scan_requires_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/v1/receipts/scan",
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_scan_returns_structured_draft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    response = client.post(
        "/v1/receipts/scan",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "draft"
    assert payload["reconciliation"]["status"] == "ok"
    assert payload["items"][0]["name"] == "Organic Baby Spinach"
    assert payload["items"][0]["line_total"] == 3.49


@pytest.mark.parametrize(
    ("category", "duration_days", "expected_days"),
    [
        ("produce", 0, 1),
        ("seafood", 900, 3),
        ("meat", 900, 5),
        ("dairy", 900, 14),
        ("deli", 900, 5),
        ("produce", 900, 14),
        ("bakery", 900, 7),
        ("frozen", 900, 270),
        ("beverage", 900, 21),
        ("pantry_staple", 900, 365),
        ("unknown", 900, 3),
    ],
)
def test_scan_clamps_llm_storage_duration(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    duration_days: int,
    expected_days: int,
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].category = category  # type: ignore[assignment]
    assert parsed.items[0].storage is not None
    parsed.items[0].storage.duration_days = duration_days

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    response = client.post(
        "/v1/receipts/scan",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )

    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["storage"]["duration_days"] == expected_days
    assert item["shelf_life_source"] == "llm_clamped"


def test_prepare_image_downscales_to_server_limits() -> None:
    prepared = prepare_receipt_image(make_jpeg(width=2400, height=4000))
    with Image.open(BytesIO(prepared)) as image:
        assert max(image.size) <= MAX_LONG_EDGE
        assert min(image.size) <= MAX_SHORT_EDGE


def test_prepare_image_rejects_non_image() -> None:
    with pytest.raises(Exception) as exc_info:
        prepare_receipt_image(b"not an image")
    assert getattr(exc_info.value, "code", None) == "UNSUPPORTED_IMAGE"
