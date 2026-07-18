from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.main import app
from app.models import EatByWindow, ReceiptLineItem, ReceiptParse, StoragePlan
from app.routers import receipts
from app.services.images import MAX_LONG_EDGE, MAX_SHORT_EDGE, prepare_receipt_image
from app.services.receipt_store import load_receipt_draft


@pytest.fixture(autouse=True)
def configured_demo_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "test-demo-token")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(tmp_path / "freshledger.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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


def test_scan_rejects_oversized_content_length_before_reading(client: TestClient) -> None:
    response = client.post(
        "/v1/receipts/scan",
        headers={
            "Authorization": "Bearer test-demo-token",
            "Content-Length": str(10 * 1024 * 1024),
        },
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "IMAGE_TOO_LARGE"


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
    assert payload["scan_provenance"] == {
        "mode": "live",
        "ai_called": True,
        "provider": "openai",
        "model": "gpt-5.6",
        "fixture_id": None,
    }
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
    parsed.items[0].canonical_key = "long_tail_test_item"
    parsed.items[0].name = "Long tail test item"
    assert parsed.items[0].storage is not None
    if category in {"bakery", "pantry_staple"}:
        parsed.items[0].storage.method = "pantry"
    elif category == "frozen":
        parsed.items[0].storage.method = "freezer"
    if category == "pantry_staple":
        parsed.items[0].is_perishable = False
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


def test_scan_clamps_eat_by_window_to_storage_duration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].category = "seafood"  # type: ignore[assignment]
    parsed.items[0].canonical_key = "long_tail_test_item"
    parsed.items[0].name = "Long tail test item"
    assert parsed.items[0].storage is not None
    parsed.items[0].storage.duration_days = 900
    parsed.items[0].eat_by_window = EatByWindow(start_days=800, end_days=900)

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
    assert item["storage"]["duration_days"] == 3
    assert item["eat_by_window"] == {"start_days": 3, "end_days": 3}


def test_scan_clamps_quantity_and_prices_and_marks_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].qty = 900
    parsed.items[0].unit_price = 999
    parsed.items[0].line_total = -999

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
    assert item["qty"] == 100
    assert item["unit_price"] == 500
    assert item["line_total"] == -500
    assert item["needs_review"] is True
    assert response.json()["reconciliation"]["computed_items_sum"] == -500
    stored = load_receipt_draft(response.json()["receipt_id"])
    assert stored is not None
    assert stored.computed_sum_cents == -50_000


def test_scan_clears_eat_by_window_when_storage_is_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].canonical_key = "unmapped_nonperishable"
    parsed.items[0].name = "Unmapped nonperishable item"
    parsed.items[0].is_perishable = False
    parsed.items[0].storage = None
    parsed.items[0].eat_by_window = EatByWindow(start_days=1, end_days=900)

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
    assert item["storage"] is None
    assert item["eat_by_window"] is None
    assert item["storage_options"] is None
    assert item["shelf_life_source"] is None


def test_scan_clears_contradictory_storage_for_non_food(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].category = "non_food"
    parsed.items[0].is_perishable = True
    parsed.items[0].storage = StoragePlan(method="pantry", temp_c=20, duration_days=900)
    parsed.items[0].eat_by_window = EatByWindow(start_days=800, end_days=900)

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
    assert item["excluded"] is True
    assert item["is_perishable"] is False
    assert item["canonical_key"] is None
    assert item["storage"] is None
    assert item["eat_by_window"] is None


def test_demo_scan_is_zero_token_and_strictly_labeled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_if_called(_jpeg_bytes: bytes) -> ReceiptParse:
        raise AssertionError("Demo mode must never call the live vision service")

    monkeypatch.setattr(receipts, "parse_receipt_image", fail_if_called)
    response = client.post(
        "/v1/demo/receipts/r1/scan",
        headers={"Authorization": "Bearer test-demo-token"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["scan_provenance"] == {
        "mode": "demo",
        "ai_called": False,
        "provider": None,
        "model": None,
        "fixture_id": "r1",
    }
    assert payload["receipt_id"] > 0
    assert payload["reconciliation"]["status"] == "ok"
    assert all(
        item["eat_by_window"]["end_days"] <= item["storage"]["duration_days"]
        for item in payload["items"]
        if item["storage"] is not None and item["eat_by_window"] is not None
    )


@pytest.mark.parametrize("sample_id", ["r1", "r2", "r3"])
def test_each_demo_receipt_reconciles_and_has_grounded_storage(
    client: TestClient, sample_id: str
) -> None:
    response = client.post(
        f"/v1/demo/receipts/{sample_id}/scan",
        headers={"Authorization": "Bearer test-demo-token"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["reconciliation"]["status"] == "ok"
    assert payload["items"]
    for item in payload["items"]:
        if item["excluded"]:
            assert item["storage"] is None
            continue
        assert item["storage"] is not None
        assert item["storage_options"] is not None
        assert item["shelf_life_source"] == "reference"


def test_unknown_demo_sample_uses_error_envelope(client: TestClient) -> None:
    response = client.post(
        "/v1/demo/receipts/not-real/scan",
        headers={"Authorization": "Bearer test-demo-token"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SAMPLE_NOT_FOUND"
    assert response.json()["error"]["user_message"]


def test_demo_review_confirm_and_pantry_flow(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post("/v1/demo/receipts/r2/scan", headers=headers)
    assert scan.status_code == 201
    draft = scan.json()

    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={
            "store_name": draft["store_name"],
            "purchased_at": draft["purchased_at"],
            "items": [
                {
                    "item_id": item["item_id"],
                    "name": item["name"],
                    "qty": item["qty"],
                    "unit": item["unit"],
                    "unit_price": item["unit_price"],
                    "category": item["category"],
                    "excluded": item["excluded"],
                    "storage_method_override": (
                        item["storage"]["method"] if item["storage"] else None
                    ),
                }
                for item in draft["items"]
            ],
        },
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "confirmed"
    assert confirm.json()["pantry_items_created"] > 0

    ledger = client.get("/v1/receipts", headers=headers)
    assert ledger.status_code == 200
    assert ledger.json()["summary"]["receipt_count"] == 1
    assert ledger.json()["receipts"][0]["receipt_id"] == draft["receipt_id"]

    pantry = client.get("/v1/pantry?status=active", headers=headers)
    assert pantry.status_code == 200
    pantry_payload = pantry.json()
    assert len(pantry_payload["items"]) == confirm.json()["pantry_items_created"]
    assert pantry_payload["value_in_stock"] > 0
    assert sum(pantry_payload["counts"].values()) == len(pantry_payload["items"])

    first_id = pantry_payload["items"][0]["pantry_item_id"]
    spoiled = client.post(
        f"/v1/pantry/{first_id}/spoil", headers=headers, json={"portion": 1}
    )
    assert spoiled.status_code == 200
    assert spoiled.json()["status"] == "spoiled"
    assert spoiled.json()["waste_event"]["cost_lost"] > 0


def test_framework_404_uses_user_message_envelope(client: TestClient) -> None:
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["user_message"]


def test_admin_reset_clears_repeatable_demo_state(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-demo-token"}
    first = client.post("/v1/demo/receipts/r1/scan", headers=headers)
    assert first.status_code == 201

    missing_admin = client.post("/v1/demo/reset", headers=headers)
    assert missing_admin.status_code == 401

    reset = client.post(
        "/v1/demo/reset",
        headers={**headers, "X-Admin-Token": "test-admin-token"},
    )
    assert reset.status_code == 200
    assert reset.json() == {"reset": True}
    assert client.get("/v1/receipts", headers=headers).json()["receipts"] == []
    assert client.get("/v1/pantry", headers=headers).json()["items"] == []

    second = client.post("/v1/demo/receipts/r1/scan", headers=headers)
    assert second.status_code == 201
    assert second.json()["receipt_id"] == 1


def test_prepare_image_downscales_to_server_limits() -> None:
    prepared = prepare_receipt_image(make_jpeg(width=2400, height=4000))
    with Image.open(BytesIO(prepared)) as image:
        assert max(image.size) <= MAX_LONG_EDGE
        assert min(image.size) <= MAX_SHORT_EDGE


def test_prepare_image_rejects_non_image() -> None:
    with pytest.raises(Exception) as exc_info:
        prepare_receipt_image(b"not an image")
    assert getattr(exc_info.value, "code", None) == "UNSUPPORTED_IMAGE"


def test_prepare_image_rejects_unrecognized_ftyp_brand() -> None:
    fake_iso_container = b"\x00\x00\x00\x18ftypmp42" + (b"\x00" * 32)
    with pytest.raises(Exception) as exc_info:
        prepare_receipt_image(fake_iso_container)
    assert getattr(exc_info.value, "code", None) == "UNSUPPORTED_IMAGE"
