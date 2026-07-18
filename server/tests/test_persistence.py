from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path
from datetime import date, timedelta

import pytest

from app.db import init_database
from app.models import (
    EatByWindow,
    ReceiptDraftItem,
    ReceiptLineItem,
    ReceiptParse,
    StorageOptions,
    StoragePlan,
)
from app.services.receipt_store import (
    StoreConflictError,
    StoreValidationError,
    confirm_receipt,
    consume_pantry_item,
    find_product_alias,
    list_pantry_items,
    load_receipt_draft,
    persist_receipt_draft,
    spoil_pantry_item,
    upsert_product_alias,
)


def parsed_receipt() -> ReceiptParse:
    return ReceiptParse(
        store_name="Fresh Basket Market",
        purchased_at="2026-07-18",
        subtotal=12.47,
        tax=0,
        total=12.47,
        overall_confidence=0.98,
        image_quality_issue=None,
        items=[
            ReceiptLineItem(
                raw_text="ORG BABY SPIN 5OZ",
                name="Organic Baby Spinach",
                canonical_key="spinach",
                qty=1,
                unit="each",
                unit_price=3.5,
                line_total=3.5,
                category="produce",
                is_perishable=True,
                storage=StoragePlan(method="fridge", temp_c=4, duration_days=5),
                eat_by_window=EatByWindow(start_days=0, end_days=3),
                confidence=0.98,
                needs_review=False,
            ),
            ReceiptLineItem(
                raw_text="PAPER TOWELS 6PK",
                name="Paper Towels",
                canonical_key=None,
                qty=1,
                unit="pack",
                unit_price=8.97,
                line_total=8.97,
                category="non_food",
                is_perishable=False,
                storage=None,
                eat_by_window=None,
                confidence=0.99,
                needs_review=False,
            ),
        ],
    )


def resolved_items(item_ids: tuple[int, ...]) -> list[ReceiptDraftItem]:
    parsed = parsed_receipt()
    return [
        ReceiptDraftItem(
            **parsed.items[0].model_dump(),
            item_id=item_ids[0],
            excluded=False,
            storage_options=StorageOptions(
                fridge_days=5, freezer_days=90, pantry_days=None
            ),
            shelf_life_source="reference",
        ),
        ReceiptDraftItem(
            **parsed.items[1].model_dump(),
            item_id=item_ids[1],
            excluded=True,
            storage_options=None,
            shelf_life_source=None,
        ),
    ]


def test_init_database_configures_schema_and_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "freshledger.db"
    assert init_database(path) == path

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert {
        "receipts",
        "receipt_items",
        "pantry_items",
        "waste_events",
        "meal_suggestions",
        "insights_cache",
        "shelf_life_reference",
        "product_aliases",
    } <= tables
    assert journal_mode == "wal"


def test_persist_and_load_draft_uses_cents_and_real_ids(tmp_path: Path) -> None:
    path = tmp_path / "freshledger.db"
    first = persist_receipt_draft(
        parsed_receipt(), image_hash="receipt-sha", database_path=path
    )
    duplicate = persist_receipt_draft(
        parsed_receipt(), image_hash="receipt-sha", database_path=path
    )

    assert first.created is True
    assert first.receipt_id > 0
    assert all(item_id > 0 for item_id in first.item_ids)
    assert duplicate == type(first)(first.receipt_id, first.item_ids, False)

    loaded = load_receipt_draft(first.receipt_id, database_path=path)
    assert loaded is not None
    assert loaded.status == "draft"
    assert loaded.subtotal_cents == 1247
    assert loaded.computed_sum_cents == 1247
    assert loaded.reconciliation_status == "ok"
    assert loaded.raw_parse == parsed_receipt()
    assert loaded.image_content_hash is None
    assert [item.unit_price_cents for item in loaded.items] == [350, 897]
    assert loaded.items[1].excluded is True


def test_concurrent_same_image_persistence_returns_one_draft(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.db"
    init_database(path)

    def persist() -> object:
        return persist_receipt_draft(
            parsed_receipt(), image_hash="same-image", database_path=path
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _index: persist(), range(2))

    assert first.receipt_id == second.receipt_id
    assert first.item_ids == second.item_ids
    assert sorted((first.created, second.created)) == [False, True]


def test_product_aliases_are_exact_merchant_scoped_confirmed_memory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "freshledger.db"
    first = upsert_product_alias(
        "  BNLS-CHKN   BRST ",
        canonical_key="chicken_breast",
        display_name="Chicken breast",
        category="meat",
        is_perishable=True,
        merchant_name="Fresh Basket Market",
        database_path=path,
    )
    second = upsert_product_alias(
        "bnls chkn brst",
        canonical_key="chicken_breast",
        display_name="Chicken breast",
        category="meat",
        is_perishable=True,
        merchant_name="FRESH BASKET MARKET",
        database_path=path,
    )

    assert first.confirmed_count == 1
    assert second.confirmed_count == 2
    assert find_product_alias(
        "BNLS CHKN BRST",
        merchant_name="Fresh Basket Market",
        database_path=path,
    ) == second
    assert (
        find_product_alias(
            "BNLS CHKN BRST", merchant_name="Other Store", database_path=path
        )
        is None
    )
    with pytest.raises(StoreValidationError, match="merchant"):
        upsert_product_alias(
            "BNLS CHKN BRST",
            canonical_key="raw_chicken_breast",
            display_name="Chicken breast",
            category="meat",
            is_perishable=True,
            database_path=path,
        )


def test_persist_rejects_future_purchase_date(tmp_path: Path) -> None:
    parsed = parsed_receipt().model_copy(deep=True)
    parsed.purchased_at = (date.today() + timedelta(days=1)).isoformat()

    with pytest.raises(StoreValidationError, match="future"):
        persist_receipt_draft(
            parsed,
            image_hash="future-date",
            database_path=tmp_path / "freshledger.db",
        )


def test_confirm_is_atomic_and_creates_only_food_pantry_rows(tmp_path: Path) -> None:
    path = tmp_path / "freshledger.db"
    draft = persist_receipt_draft(
        parsed_receipt(), image_hash="confirm-sha", database_path=path
    )
    result = confirm_receipt(
        draft.receipt_id,
        resolved_items(draft.item_ids),
        store_name="Edited Market",
        purchased_at="2026-07-17",
        database_path=path,
    )

    assert result.receipt_id == draft.receipt_id
    assert len(result.pantry_item_ids) == 1
    pantry = list_pantry_items(database_path=path)
    assert len(pantry) == 1
    assert pantry[0].receipt_item_id == draft.item_ids[0]
    assert pantry[0].best_by == "2026-07-20"
    assert pantry[0].safe_until == "2026-07-22"
    assert pantry[0].unit_price_cents == 350
    assert load_receipt_draft(draft.receipt_id, database_path=path).status == "confirmed"  # type: ignore[union-attr]

    with pytest.raises(StoreConflictError):
        confirm_receipt(
            draft.receipt_id, resolved_items(draft.item_ids), database_path=path
        )


def test_concurrent_confirm_creates_exactly_one_pantry_row(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-confirm.db"
    draft = persist_receipt_draft(
        parsed_receipt(), image_hash="concurrent-confirm", database_path=path
    )

    def attempt_confirm() -> str:
        try:
            confirm_receipt(
                draft.receipt_id,
                resolved_items(draft.item_ids),
                database_path=path,
            )
        except StoreConflictError:
            return "conflict"
        return "confirmed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: attempt_confirm(), range(2)))

    assert sorted(outcomes) == ["confirmed", "conflict"]
    pantry = list_pantry_items(database_path=path)
    assert len(pantry) == 1
    assert pantry[0].receipt_item_id == draft.item_ids[0]


def test_confirm_rejects_items_from_outside_the_receipt(tmp_path: Path) -> None:
    path = tmp_path / "freshledger.db"
    draft = persist_receipt_draft(
        parsed_receipt(), image_hash="wrong-items", database_path=path
    )
    items = resolved_items(draft.item_ids)
    items[0] = items[0].model_copy(update={"item_id": 99_999})

    with pytest.raises(StoreValidationError):
        confirm_receipt(draft.receipt_id, items, database_path=path)
    assert list_pantry_items(database_path=path) == ()
    assert load_receipt_draft(draft.receipt_id, database_path=path).status == "draft"  # type: ignore[union-attr]


def test_confirm_rejects_future_purchase_date(tmp_path: Path) -> None:
    path = tmp_path / "freshledger.db"
    draft = persist_receipt_draft(
        parsed_receipt(), image_hash="future-confirm", database_path=path
    )

    with pytest.raises(StoreValidationError, match="future"):
        confirm_receipt(
            draft.receipt_id,
            resolved_items(draft.item_ids),
            purchased_at=(date.today() + timedelta(days=1)).isoformat(),
            database_path=path,
        )
    assert list_pantry_items(database_path=path) == ()


def test_consume_and_spoil_follow_initial_quantity_portions(tmp_path: Path) -> None:
    path = tmp_path / "freshledger.db"
    draft = persist_receipt_draft(
        parsed_receipt(), image_hash="quantity-sha", database_path=path
    )
    result = confirm_receipt(
        draft.receipt_id, resolved_items(draft.item_ids), database_path=path
    )
    pantry_id = result.pantry_item_ids[0]

    first = consume_pantry_item(pantry_id, 0.25, database_path=path)
    assert first.qty_remaining == pytest.approx(0.75)
    assert first.status == "active"

    spoiled = spoil_pantry_item(pantry_id, 0.25, database_path=path)
    assert spoiled.qty_remaining == pytest.approx(0.5)
    assert spoiled.status == "active"
    assert spoiled.cost_lost_cents == 88
    assert spoiled.waste_event_id is not None

    final = consume_pantry_item(pantry_id, 0.5, database_path=path)
    assert final.qty_remaining == pytest.approx(0)
    assert final.status == "eaten"
    assert list_pantry_items(database_path=path) == ()
    assert list_pantry_items(status="eaten", database_path=path)[0].id == pantry_id

    with pytest.raises(StoreConflictError):
        spoil_pantry_item(pantry_id, database_path=path)
