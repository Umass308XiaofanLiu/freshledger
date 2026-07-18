from __future__ import annotations

import pytest

from app.models import EatByWindow, ReceiptLineItem, StoragePlan
from app.services.shelf_life import load_reference_rows, resolve_item


def test_reference_table_has_100_valid_unique_rows() -> None:
    rows = load_reference_rows()
    assert len(rows) >= 100
    assert len({row.canonical_key for row in rows}) == len(rows)
    for row in rows:
        duration = row.days_for(row.recommended_method)
        assert duration is not None
        assert 1 <= duration <= 730
        if row.best_by_days is not None:
            assert row.eat_by_start_days <= row.best_by_days <= duration


def test_cooked_rice_cannot_match_dry_rice_reference() -> None:
    cooked_rice = ReceiptLineItem(
        raw_text="COOKED WHITE RICE",
        name="Cooked white rice",
        canonical_key="white_rice",
        qty=1,
        unit="each",
        unit_price=3.99,
        line_total=3.99,
        category="deli",
        is_perishable=True,
        storage=StoragePlan(method="fridge", temp_c=4, duration_days=4),
        eat_by_window=EatByWindow(start_days=1, end_days=4),
        confidence=0.8,
        needs_review=False,
    )

    resolved = resolve_item(cooked_rice, item_id=1)
    assert resolved.shelf_life_source == "llm_clamped"
    assert resolved.storage is not None
    assert resolved.storage.method == "fridge"
    assert resolved.storage.duration_days <= 5
    assert resolved.eat_by_window is not None
    assert resolved.eat_by_window.end_days <= resolved.storage.duration_days


def test_perishable_pantry_staple_without_storage_uses_unknown_fridge_default() -> None:
    cooked_rice = ReceiptLineItem(
        raw_text="COOKED WHITE RICE",
        name="Cooked white rice",
        canonical_key="white_rice",
        qty=1,
        unit="each",
        unit_price=3.99,
        line_total=3.99,
        category="pantry_staple",
        is_perishable=True,
        storage=None,
        eat_by_window=None,
        confidence=0.6,
        needs_review=False,
    )

    resolved = resolve_item(cooked_rice, item_id=1)
    assert resolved.shelf_life_source == "default"
    assert resolved.needs_review is True
    assert resolved.storage is not None
    assert resolved.storage.method == "fridge"
    assert resolved.storage.duration_days <= 3


@pytest.mark.parametrize(
    ("name", "canonical_key"),
    [("Rice pudding", "rice_pudding"), ("Pasta salad", "pasta_salad")],
)
def test_perishable_pantry_staple_cannot_match_dry_reference(
    name: str, canonical_key: str
) -> None:
    item = ReceiptLineItem(
        raw_text=name.upper(),
        name=name,
        canonical_key=canonical_key,
        qty=1,
        unit="each",
        unit_price=4.99,
        line_total=4.99,
        category="pantry_staple",
        is_perishable=True,
        storage=StoragePlan(method="pantry", temp_c=20, duration_days=365),
        eat_by_window=EatByWindow(start_days=270, end_days=365),
        confidence=0.6,
        needs_review=False,
    )

    resolved = resolve_item(item, item_id=1)
    assert resolved.shelf_life_source == "default"
    assert resolved.needs_review is True
    assert resolved.storage is not None
    assert resolved.storage.method == "fridge"
    assert resolved.storage.duration_days <= 3


def test_long_tail_meat_rejects_llm_pantry_and_uses_fridge_default() -> None:
    raw_meat = ReceiptLineItem(
        raw_text="MYSTERY RAW MEAT",
        name="Mystery raw meat",
        canonical_key="mystery_raw_meat",
        qty=1,
        unit="each",
        unit_price=8.99,
        line_total=8.99,
        category="meat",
        is_perishable=True,
        storage=StoragePlan(method="pantry", temp_c=20, duration_days=5),
        eat_by_window=EatByWindow(start_days=1, end_days=5),
        confidence=0.6,
        needs_review=False,
    )

    resolved = resolve_item(raw_meat, item_id=1)
    assert resolved.shelf_life_source == "default"
    assert resolved.needs_review is True
    assert resolved.storage is not None
    assert resolved.storage.method == "fridge"
    assert resolved.storage.duration_days <= 2
    assert resolved.eat_by_window is not None
    assert resolved.eat_by_window.end_days <= resolved.storage.duration_days


def test_long_tail_perishable_beverage_rejects_llm_pantry() -> None:
    juice = ReceiptLineItem(
        raw_text="FRESH PRESSED JUICE",
        name="Fresh pressed juice",
        canonical_key="fresh_pressed_juice",
        qty=1,
        unit="each",
        unit_price=6.99,
        line_total=6.99,
        category="beverage",
        is_perishable=True,
        storage=StoragePlan(method="pantry", temp_c=20, duration_days=21),
        eat_by_window=EatByWindow(start_days=7, end_days=21),
        confidence=0.6,
        needs_review=False,
    )

    resolved = resolve_item(juice, item_id=1)
    assert resolved.shelf_life_source == "default"
    assert resolved.needs_review is True
    assert resolved.storage is not None
    assert resolved.storage.method == "fridge"
    assert resolved.storage.duration_days <= 7


def test_long_tail_does_not_advertise_ungrounded_location_override() -> None:
    dragon_fruit = ReceiptLineItem(
        raw_text="DRAGON FRUIT",
        name="Dragon fruit",
        canonical_key="dragon_fruit",
        qty=1,
        unit="each",
        unit_price=4.99,
        line_total=4.99,
        category="produce",
        is_perishable=True,
        storage=StoragePlan(method="fridge", temp_c=4, duration_days=10),
        eat_by_window=EatByWindow(start_days=5, end_days=10),
        confidence=0.8,
        needs_review=False,
    )

    scanned = resolve_item(dragon_fruit, item_id=1)
    assert scanned.storage_options is not None
    assert scanned.storage_options.freezer_days is None
    assert scanned.storage_options.pantry_days is None

    try:
        resolve_item(dragon_fruit, item_id=1, method_override="freezer")
    except Exception as exc:
        assert getattr(exc, "code", None) == "STORAGE_NOT_RECOMMENDED"
    else:
        raise AssertionError("Ungrounded freezer override must be rejected")


def test_storage_null_perishable_does_not_advertise_other_locations() -> None:
    dragon_fruit = ReceiptLineItem(
        raw_text="DRAGON FRUIT",
        name="Dragon fruit",
        canonical_key="dragon_fruit",
        qty=1,
        unit="each",
        unit_price=4.99,
        line_total=4.99,
        category="produce",
        is_perishable=True,
        storage=None,
        eat_by_window=EatByWindow(start_days=900, end_days=900),
        confidence=0.6,
        needs_review=True,
    )

    scanned = resolve_item(dragon_fruit, item_id=1)
    assert scanned.storage_options is not None
    assert scanned.storage_options.freezer_days is None
    assert scanned.storage_options.pantry_days is None

    try:
        resolve_item(dragon_fruit, item_id=1, method_override="freezer")
    except Exception as exc:
        assert getattr(exc, "code", None) == "STORAGE_NOT_RECOMMENDED"
    else:
        raise AssertionError("Ungrounded freezer override must be rejected")


def test_reference_override_uses_target_location_temperature() -> None:
    bread = ReceiptLineItem(
        raw_text="SOURDOUGH LOAF",
        name="Sourdough bread",
        canonical_key="sourdough_bread",
        qty=1,
        unit="each",
        unit_price=4.99,
        line_total=4.99,
        category="bakery",
        is_perishable=True,
        storage=StoragePlan(method="pantry", temp_c=20, duration_days=4),
        eat_by_window=EatByWindow(start_days=2, end_days=4),
        confidence=0.9,
        needs_review=False,
    )

    moved = resolve_item(bread, item_id=1, method_override="fridge")
    assert moved.storage is not None
    assert moved.storage.method == "fridge"
    assert moved.storage.temp_c == 4
