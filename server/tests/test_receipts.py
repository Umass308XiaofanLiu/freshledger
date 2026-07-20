from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.db import connect, sync_shelf_life_reference
from app.main import app
from app.models import EatByWindow, ReceiptLineItem, ReceiptParse, StoragePlan
from app.routers import receipts
from app.services.images import MAX_LONG_EDGE, MAX_SHORT_EDGE, prepare_receipt_image
from app.services.receipt_store import (
    PersistDraftResult,
    find_product_alias,
    list_pantry_items,
    load_receipt_draft,
    persist_receipt_draft,
    upsert_product_alias,
)


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
        "ai_called": False,
        "provider": "rapidocr",
        "model": "PP-OCRv6-small",
        "fixture_id": None,
    }
    assert payload["reconciliation"]["status"] == "ok"
    assert payload["items"][0]["name"] == "Organic Baby Spinach"
    assert payload["items"][0]["line_total"] == 3.49


def test_openai_scan_engine_remains_optional(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "openai")
    get_settings.cache_clear()

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    response = client.post(
        "/v1/receipts/scan",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )

    assert response.status_code == 201
    assert response.json()["scan_provenance"] == {
        "mode": "live",
        "ai_called": True,
        "provider": "openai",
        "model": "gpt-5.6",
        "fixture_id": None,
    }


def test_explicit_local_scan_overrides_global_openai_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "openai")
    get_settings.cache_clear()
    observed_engine: str | None = None

    async def fake_parse(
        _jpeg_bytes: bytes, *, engine: str | None = None
    ) -> ReceiptParse:
        nonlocal observed_engine
        observed_engine = engine
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    response = client.post(
        "/v1/receipts/scan?engine=offline",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(width=603), "image/jpeg")},
    )

    assert response.status_code == 201
    assert observed_engine == "offline"
    assert response.json()["scan_provenance"] == {
        "mode": "live",
        "ai_called": False,
        "provider": "rapidocr",
        "model": "PP-OCRv6-small",
        "fixture_id": None,
    }


def test_query_parameter_cannot_force_an_openai_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden_parse(*_args: object, **_kwargs: object) -> ReceiptParse:
        raise AssertionError("an OpenAI engine query must be rejected before dispatch")

    monkeypatch.setattr(receipts, "parse_receipt_image", forbidden_parse)
    response = client.post(
        "/v1/receipts/scan?engine=openai",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(width=610), "image/jpeg")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_scan_cache_is_namespaced_by_recognizer_and_preserves_content_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    jpeg = make_jpeg()
    offline = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert offline.status_code == 201

    monkeypatch.setenv("RECEIPT_SCAN_ENGINE", "openai")
    get_settings.cache_clear()
    openai = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert openai.status_code == 201
    assert openai.json()["receipt_id"] != offline.json()["receipt_id"]

    offline_record = load_receipt_draft(offline.json()["receipt_id"])
    openai_record = load_receipt_draft(openai.json()["receipt_id"])
    assert offline_record is not None and openai_record is not None
    assert offline_record.image_hash is not None
    assert offline_record.image_hash.startswith("rapidocr:PP-OCRv6-small:")
    assert openai_record.image_hash is not None
    assert openai_record.image_hash.startswith("openai:gpt-5.6:")
    assert offline_record.image_content_hash == openai_record.image_content_hash


def test_confirmed_receipt_image_is_not_reissued_as_a_draft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    jpeg = make_jpeg(width=604)
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert scan.status_code == 201
    draft = scan.json()
    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={"items": [{"item_id": draft["items"][0]["item_id"]}]},
    )
    assert confirm.status_code == 200

    repeated = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "RECEIPT_ALREADY_IMPORTED"


@pytest.mark.parametrize("invalid_kind", ["duplicate", "extra"])
def test_confirm_rejects_duplicate_or_extra_item_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=614), "image/jpeg")},
    ).json()
    valid_item = {"item_id": scan["items"][0]["item_id"]}
    invalid_item = (
        valid_item.copy()
        if invalid_kind == "duplicate"
        else {"item_id": scan["items"][0]["item_id"] + 9999}
    )

    response = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={"items": [valid_item, invalid_item]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "DUPLICATE_ITEM" if invalid_kind == "duplicate" else "UNKNOWN_ITEM"
    )
    stored = load_receipt_draft(scan["receipt_id"])
    assert stored is not None
    assert stored.status == "draft"
    assert list_pantry_items() == ()


def test_cached_draft_applies_new_exact_confirmed_alias(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].name = "Mystery greens"
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    jpeg = make_jpeg(width=605)
    first = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert first.status_code == 201
    upsert_product_alias(
        "ORG BABY SPIN 5OZ",
        canonical_key="baby_spinach",
        display_name="Spinach",
        category="produce",
        is_perishable=True,
        merchant_name="Test Market",
    )

    cached = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", jpeg, "image/jpeg")},
    )
    assert cached.status_code == 201
    assert cached.json()["receipt_id"] == first.json()["receipt_id"]
    assert cached.json()["items"][0]["name"] == "Spinach"
    assert cached.json()["items"][0]["canonical_key"] == "baby_spinach"


def test_concurrent_scan_loser_returns_winners_stored_parse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner_parse = parsed_receipt()
    loser_parse = parsed_receipt().model_copy(deep=True)
    loser_parse.items.append(
        loser_parse.items[0].model_copy(
            update={
                "raw_text": "EXTRA LOSER ITEM",
                "name": "Extra loser item",
                "canonical_key": None,
                "category": "unknown",
                "unit_price": 1.0,
                "line_total": 1.0,
                "needs_review": True,
            }
        )
    )

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return loser_parse

    def concurrent_winner(
        _parsed: ReceiptParse, **kwargs: object
    ) -> PersistDraftResult:
        created = persist_receipt_draft(
            winner_parse,
            image_hash=str(kwargs["image_hash"]),
            image_content_hash=str(kwargs["image_content_hash"]),
            purchased_at_fallback=str(kwargs["purchased_at_fallback"]),
        )
        return PersistDraftResult(created.receipt_id, created.item_ids, False)

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    monkeypatch.setattr(receipts, "persist_receipt_draft", concurrent_winner)
    response = client.post(
        "/v1/receipts/scan",
        headers={"Authorization": "Bearer test-demo-token"},
        files={"image": ("receipt.jpg", make_jpeg(width=613), "image/jpeg")},
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Organic Baby Spinach"
    stored = load_receipt_draft(body["receipt_id"])
    assert stored is not None
    assert len(stored.items) == 1


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


def test_confirm_learns_only_an_explicit_grounded_name_correction(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(), "image/jpeg")},
    )
    assert scan.status_code == 201
    draft = scan.json()

    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": draft["items"][0]["item_id"],
                    "name": "Spinach",
                }
            ]
        },
    )
    assert confirm.status_code == 200
    learned = find_product_alias(
        "Organic Baby Spinach", merchant_name="Test Market"
    )
    assert learned is not None
    assert learned.canonical_key == "baby_spinach"
    assert learned.display_name == "Spinach"
    assert learned.confirmed_count == 1


def test_confirm_does_not_learn_from_unchanged_form_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed_receipt()

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=601), "image/jpeg")},
    )
    assert scan.status_code == 201
    draft = scan.json()
    item = draft["items"][0]

    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": item["item_id"],
                    "name": item["name"],
                    "qty": item["qty"],
                    "unit": item["unit"],
                    "unit_price": item["unit_price"],
                    "category": item["category"],
                    "excluded": item["excluded"],
                }
            ]
        },
    )
    assert confirm.status_code == 200
    assert (
        find_product_alias("Organic Baby Spinach", merchant_name="Test Market")
        is None
    )


def test_unmatched_name_correction_cannot_inherit_old_safety_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].name = "Dry white rice"
    parsed.items[0].canonical_key = "white_rice"
    parsed.items[0].category = "pantry_staple"
    parsed.items[0].is_perishable = False
    parsed.items[0].storage = StoragePlan(
        method="pantry", temp_c=20, duration_days=365
    )
    parsed.items[0].eat_by_window = None

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=602), "image/jpeg")},
    )
    assert scan.status_code == 201
    draft = scan.json()

    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {"item_id": draft["items"][0]["item_id"], "name": "Rice pudding"}
            ]
        },
    )
    assert confirm.status_code == 200

    stored = load_receipt_draft(draft["receipt_id"])
    assert stored is not None
    corrected = stored.items[0]
    assert corrected.canonical_key is None
    assert corrected.category == "unknown"
    assert corrected.is_perishable is True
    assert corrected.needs_review is True
    pantry_items = list_pantry_items()
    assert len(pantry_items) == 1
    assert pantry_items[0].category == "unknown"
    assert pantry_items[0].shelf_life_source == "default"
    assert pantry_items[0].storage_duration_days == 3
    assert (
        find_product_alias("Dry white rice", merchant_name="Test Market") is None
    )


def test_category_correction_regrounds_without_stale_storage_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].name = "Mystery produce"
    parsed.items[0].canonical_key = "white_rice"
    parsed.items[0].category = "pantry_staple"
    parsed.items[0].is_perishable = False
    parsed.items[0].storage = StoragePlan(
        method="pantry", temp_c=20, duration_days=365
    )
    parsed.items[0].eat_by_window = None

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=609), "image/jpeg")},
    ).json()
    confirm = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": scan["items"][0]["item_id"],
                    "category": "produce",
                    "storage_method_override": "pantry",
                }
            ]
        },
    )
    assert confirm.status_code == 200
    stored = load_receipt_draft(scan["receipt_id"])
    assert stored is not None
    corrected = stored.items[0]
    assert corrected.canonical_key is None
    assert corrected.category == "produce"
    assert corrected.is_perishable is True
    pantry_items = list_pantry_items()
    assert len(pantry_items) == 1
    assert pantry_items[0].storage_method == "fridge"
    assert pantry_items[0].storage_duration_days == 5


def test_conflicting_name_and_category_correction_cannot_learn_unsafe_alias(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].name = "Mystery Sku"
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=611), "image/jpeg")},
    ).json()
    confirm = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": scan["items"][0]["item_id"],
                    "name": "Rice",
                    "category": "dairy",
                }
            ]
        },
    )
    assert confirm.status_code == 200
    stored = load_receipt_draft(scan["receipt_id"])
    assert stored is not None
    corrected = stored.items[0]
    assert corrected.canonical_key is None
    assert corrected.category == "dairy"
    assert corrected.is_perishable is True
    assert corrected.needs_review is True
    pantry_items = list_pantry_items()
    assert len(pantry_items) == 1
    assert pantry_items[0].storage_method == "fridge"
    assert pantry_items[0].storage_duration_days == 7
    assert find_product_alias("Mystery Sku", merchant_name="Test Market") is None


def test_unmatched_name_and_category_correction_uses_default_without_learning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].name = "Mystery Juice Sku"
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=612), "image/jpeg")},
    ).json()
    confirm = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": scan["items"][0]["item_id"],
                    "name": "Apple juice",
                    "category": "produce",
                }
            ]
        },
    )

    assert confirm.status_code == 200
    stored = load_receipt_draft(scan["receipt_id"])
    assert stored is not None
    corrected = stored.items[0]
    assert corrected.name == "Apple juice"
    assert corrected.canonical_key is None
    assert corrected.category == "produce"
    assert corrected.needs_review is True
    pantry_items = list_pantry_items()
    assert len(pantry_items) == 1
    assert pantry_items[0].shelf_life_source == "default"
    assert pantry_items[0].storage_method == "fridge"
    assert pantry_items[0].storage_duration_days <= 5
    assert (
        find_product_alias("Mystery Juice Sku", merchant_name="Test Market")
        is None
    )


def test_confirm_recomputes_line_total_receipt_sum_and_ledger_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.subtotal = None
    parsed.tax = None
    parsed.total = None

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=606), "image/jpeg")},
    )
    draft = scan.json()
    confirm = client.post(
        f"/v1/receipts/{draft['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": draft["items"][0]["item_id"],
                    "qty": 2,
                    "unit_price": 2.99,
                }
            ]
        },
    )
    assert confirm.status_code == 200
    assert confirm.json()["ledger_total"] == 5.98

    stored = load_receipt_draft(draft["receipt_id"])
    assert stored is not None
    assert stored.items[0].qty == 2
    assert stored.items[0].unit_price_cents == 299
    assert stored.items[0].line_total_cents == 598
    assert stored.computed_sum_cents == 598
    assert stored.reconciliation_status == "unreadable"


def test_alias_learning_requires_merchant_and_is_best_effort(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.store_name = None
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("merchantless corrections must not create global aliases")

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    monkeypatch.setattr(receipts, "upsert_product_alias", fail_if_called)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=607), "image/jpeg")},
    ).json()
    confirm = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {"item_id": scan["items"][0]["item_id"], "name": "Spinach"}
            ]
        },
    )
    assert confirm.status_code == 200


def test_alias_storage_failure_does_not_undo_a_confirmed_receipt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parsed_receipt()
    parsed.items[0].canonical_key = None
    parsed.items[0].category = "unknown"

    async def fake_parse(_jpeg_bytes: bytes) -> ReceiptParse:
        return parsed

    def fail_alias_write(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated alias write failure")

    monkeypatch.setattr(receipts, "parse_receipt_image", fake_parse)
    monkeypatch.setattr(receipts, "upsert_product_alias", fail_alias_write)
    headers = {"Authorization": "Bearer test-demo-token"}
    scan = client.post(
        "/v1/receipts/scan",
        headers=headers,
        files={"image": ("receipt.jpg", make_jpeg(width=608), "image/jpeg")},
    ).json()
    confirm = client.post(
        f"/v1/receipts/{scan['receipt_id']}/confirm",
        headers=headers,
        json={
            "items": [
                {"item_id": scan["items"][0]["item_id"], "name": "Spinach"}
            ]
        },
    )
    assert confirm.status_code == 200
    stored = load_receipt_draft(scan["receipt_id"])
    assert stored is not None
    assert stored.status == "confirmed"


def test_framework_404_uses_user_message_envelope(client: TestClient) -> None:
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["user_message"]


def test_admin_reset_clears_repeatable_demo_state(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-demo-token"}
    first = client.post("/v1/demo/receipts/r1/scan", headers=headers)
    assert first.status_code == 201
    upsert_product_alias(
        "ORG BABY SPIN 5OZ",
        canonical_key="spinach",
        display_name="Spinach",
        category="produce",
        is_perishable=True,
        merchant_name="Fixture Market",
    )

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
    assert find_product_alias(
        "ORG BABY SPIN 5OZ", merchant_name="Fixture Market"
    ) is not None

    second = client.post("/v1/demo/receipts/r1/scan", headers=headers)
    assert second.status_code == 201
    assert second.json()["receipt_id"] == 1


def test_user_clear_requires_exact_confirmation_before_deleting(
    client: TestClient,
) -> None:
    headers = {"Authorization": "Bearer test-demo-token"}
    first = client.post("/v1/demo/receipts/r1/scan", headers=headers)
    assert first.status_code == 201

    unauthorized = client.post(
        "/v1/demo/clear",
        json={"confirmation": "RESET_ALL_DATA"},
    )
    missing = client.post("/v1/demo/clear", headers=headers, json={})
    wrong = client.post(
        "/v1/demo/clear",
        headers=headers,
        json={"confirmation": "reset_all_data"},
    )
    assert unauthorized.status_code == 401
    assert missing.status_code == 422
    assert wrong.status_code == 422
    assert client.get("/v1/receipts", headers=headers).json()["receipts"] == []
    assert load_receipt_draft(first.json()["receipt_id"]) is not None


def test_user_clear_atomically_removes_all_mutable_user_data(
    client: TestClient,
) -> None:
    headers = {"Authorization": "Bearer test-demo-token"}
    seeded = client.post(
        "/v1/demo/seed",
        headers=headers,
        json={"profile": "judge"},
    )
    assert seeded.status_code == 200
    pantry_id = client.get("/v1/pantry", headers=headers).json()["items"][0][
        "pantry_item_id"
    ]
    spoiled = client.post(
        f"/v1/pantry/{pantry_id}/spoil",
        headers=headers,
        json={"portion": 1},
    )
    assert spoiled.status_code == 200
    upsert_product_alias(
        "ORG BABY SPIN 5OZ",
        canonical_key="spinach",
        display_name="Spinach",
        category="produce",
        is_perishable=True,
        merchant_name="Fixture Market",
    )
    reference_count = sync_shelf_life_reference()
    with connect() as connection, connection:
        connection.execute(
            "INSERT INTO insights_cache (data_hash, payload_json) VALUES (?, ?)",
            ("clear-test", "{}"),
        )
        connection.execute(
            "INSERT INTO ai_call_usage (operation) VALUES (?)",
            ("clear-test",),
        )

    response = client.post(
        "/v1/demo/clear",
        headers=headers,
        json={"confirmation": "RESET_ALL_DATA"},
    )

    assert response.status_code == 200
    deleted = response.json()["deleted"]
    assert response.json()["reset"] is True
    assert deleted["receipts"] == 3
    assert deleted["receipt_items"] > 0
    assert deleted["pantry_items"] > 0
    assert deleted["waste_events"] == 1
    assert deleted["meal_suggestions"] == 1
    assert deleted["insights_cache"] == 1
    assert deleted["product_aliases"] == 1

    with connect() as connection:
        for table in deleted:
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shelf_life_reference"
        ).fetchone()[0] == reference_count
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_call_usage"
        ).fetchone()[0] == 1


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
