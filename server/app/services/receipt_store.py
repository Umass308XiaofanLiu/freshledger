from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

from ..db import connect, init_database
from ..models import ReceiptDraftItem, ReceiptLineItem, ReceiptParse


class ReceiptStoreError(Exception):
    """Base class for persistence errors that route handlers can map to AppError."""


class RecordNotFoundError(ReceiptStoreError):
    pass


class StoreConflictError(ReceiptStoreError):
    pass


class StoreValidationError(ReceiptStoreError):
    pass


@dataclass(frozen=True)
class PersistDraftResult:
    receipt_id: int
    item_ids: tuple[int, ...]
    created: bool


@dataclass(frozen=True)
class ReceiptItemRecord:
    id: int
    receipt_id: int
    raw_text: str
    name: str
    canonical_key: str | None
    qty: float
    unit: str
    unit_price_cents: int
    line_total_cents: int
    category: str
    is_perishable: bool
    confidence: float
    needs_review: bool
    excluded: bool


@dataclass(frozen=True)
class ReceiptDraftRecord:
    id: int
    store_name: str | None
    purchased_at: str
    image_hash: str | None
    image_content_hash: str | None
    subtotal_cents: int | None
    tax_cents: int | None
    total_cents: int | None
    computed_sum_cents: int
    reconciliation_status: str
    overall_confidence: float | None
    status: str
    raw_parse: ReceiptParse | None
    items: tuple[ReceiptItemRecord, ...]


@dataclass(frozen=True)
class ConfirmResult:
    receipt_id: int
    pantry_item_ids: tuple[int, ...]
    ledger_total_cents: int


@dataclass(frozen=True)
class PantryItemRecord:
    id: int
    receipt_item_id: int | None
    name: str
    canonical_key: str | None
    category: str
    qty_initial: float
    qty_remaining: float
    unit: str
    unit_price_cents: int
    storage_method: str
    storage_temp_c: float | None
    storage_duration_days: int
    shelf_life_source: str
    purchased_at: str
    best_by: str
    safe_until: str
    status: str
    updated_at: str


@dataclass(frozen=True)
class PantryMutationResult:
    pantry_item_id: int
    qty_remaining: float
    status: str
    waste_event_id: int | None = None
    cost_lost_cents: int | None = None
    occurred_at: str | None = None


@dataclass(frozen=True)
class ProductAliasRecord:
    id: int
    normalized_raw_line: str
    merchant_key: str
    raw_line: str
    merchant_name: str | None
    canonical_key: str
    display_name: str
    category: str
    is_perishable: bool
    confirmed_count: int


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _to_cents(value: float | None) -> int | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def normalize_product_alias_key(value: str) -> str:
    """Normalize an OCR description for conservative exact alias matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _row_to_product_alias(row: sqlite3.Row) -> ProductAliasRecord:
    return ProductAliasRecord(
        id=row["id"],
        normalized_raw_line=row["normalized_raw_line"],
        merchant_key=row["merchant_key"],
        raw_line=row["raw_line"],
        merchant_name=row["merchant_name"],
        canonical_key=row["canonical_key"],
        display_name=row["display_name"],
        category=row["category"],
        is_perishable=bool(row["is_perishable"]),
        confirmed_count=row["confirmed_count"],
    )


def upsert_product_alias(
    raw_text: str,
    *,
    canonical_key: str,
    display_name: str,
    category: str,
    is_perishable: bool,
    merchant_name: str | None = None,
    database_path: str | Path | None = None,
) -> ProductAliasRecord:
    """Remember one explicit user correction for an exact parsed description.

    Callers are responsible for invoking this only after a human confirmation;
    recognizer guesses must never train this table.
    """

    normalized_raw_line = normalize_product_alias_key(raw_text)
    merchant_key = normalize_product_alias_key(merchant_name or "")
    if not normalized_raw_line:
        raise StoreValidationError("product alias raw_text cannot be empty")
    if not merchant_key:
        raise StoreValidationError("product aliases require a merchant name")
    if not canonical_key.strip() or not display_name.strip() or not category.strip():
        raise StoreValidationError("product alias target fields cannot be empty")

    init_database(database_path)
    with connect(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO product_aliases (
              normalized_raw_line, merchant_key, raw_line, merchant_name,
              canonical_key, display_name, category, is_perishable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_raw_line, merchant_key) DO UPDATE SET
              raw_line=excluded.raw_line,
              merchant_name=excluded.merchant_name,
              canonical_key=excluded.canonical_key,
              display_name=excluded.display_name,
              category=excluded.category,
              is_perishable=excluded.is_perishable,
              confirmed_count=product_aliases.confirmed_count + 1,
              updated_at=datetime('now')
            """,
            (
                normalized_raw_line,
                merchant_key,
                raw_text.strip(),
                merchant_name.strip() if merchant_name else None,
                canonical_key.strip(),
                display_name.strip(),
                category.strip(),
                int(is_perishable),
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM product_aliases
            WHERE normalized_raw_line = ? AND merchant_key = ?
            """,
            (normalized_raw_line, merchant_key),
        ).fetchone()
    if row is None:  # pragma: no cover - guarded by the successful upsert
        raise StoreConflictError("product alias was not persisted")
    return _row_to_product_alias(row)


def find_product_alias(
    raw_text: str,
    *,
    merchant_name: str | None = None,
    database_path: str | Path | None = None,
) -> ProductAliasRecord | None:
    """Find a merchant-specific exact alias."""

    normalized_raw_line = normalize_product_alias_key(raw_text)
    merchant_key = normalize_product_alias_key(merchant_name or "")
    if not normalized_raw_line or not merchant_key:
        return None
    init_database(database_path)
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT * FROM product_aliases
            WHERE normalized_raw_line = ? AND merchant_key = ?
            LIMIT 1
            """,
            (normalized_raw_line, merchant_key),
        ).fetchone()
    return _row_to_product_alias(row) if row is not None else None


def _reconciliation(
    parsed: ReceiptParse, items: Sequence[ReceiptLineItem]
) -> tuple[int, str]:
    computed_sum = sum(_to_cents(item.line_total) or 0 for item in items)
    if parsed.subtotal is not None:
        status = "ok" if abs(computed_sum - (_to_cents(parsed.subtotal) or 0)) <= 5 else "mismatch"
    elif parsed.total is not None:
        expected = computed_sum + (_to_cents(parsed.tax) or 0)
        status = "ok" if abs(expected - (_to_cents(parsed.total) or 0)) <= 5 else "mismatch"
    else:
        status = "unreadable"
    return computed_sum, status


def _row_to_receipt_item(row: sqlite3.Row) -> ReceiptItemRecord:
    return ReceiptItemRecord(
        id=row["id"],
        receipt_id=row["receipt_id"],
        raw_text=row["raw_text"],
        name=row["name"],
        canonical_key=row["canonical_key"],
        qty=row["qty"],
        unit=row["unit"],
        unit_price_cents=row["unit_price_cents"],
        line_total_cents=row["line_total_cents"],
        category=row["category"],
        is_perishable=bool(row["is_perishable"]),
        confidence=row["confidence"],
        needs_review=bool(row["needs_review"]),
        excluded=bool(row["excluded"]),
    )


def _row_to_pantry_item(row: sqlite3.Row) -> PantryItemRecord:
    return PantryItemRecord(
        id=row["id"],
        receipt_item_id=row["receipt_item_id"],
        name=row["name"],
        canonical_key=row["canonical_key"],
        category=row["category"],
        qty_initial=row["qty_initial"],
        qty_remaining=row["qty_remaining"],
        unit=row["unit"],
        unit_price_cents=row["unit_price_cents"],
        storage_method=row["storage_method"],
        storage_temp_c=row["storage_temp_c"],
        storage_duration_days=row["storage_duration_days"],
        shelf_life_source=row["shelf_life_source"],
        purchased_at=row["purchased_at"],
        best_by=row["best_by"],
        safe_until=row["safe_until"],
        status=row["status"],
        updated_at=row["updated_at"],
    )


def persist_receipt_draft(
    parsed: ReceiptParse,
    *,
    image_hash: str | None,
    image_content_hash: str | None = None,
    resolved_items: Sequence[ReceiptLineItem] | None = None,
    purchased_at_fallback: str | None = None,
    database_path: str | Path | None = None,
) -> PersistDraftResult:
    """Persist a parsed live or demo receipt and return database-backed item IDs.

    A non-null image hash makes the operation idempotent. ``resolved_items`` may
    contain ReceiptDraftItem instances so grounded canonical keys and exclusions
    are stored without changing the original GPT payload kept in raw_model_json.
    """

    items = tuple(resolved_items or parsed.items)
    if len(items) != len(parsed.items):
        raise StoreValidationError("resolved_items must correspond to every parsed item")

    purchased_at = parsed.purchased_at or purchased_at_fallback or date.today().isoformat()
    try:
        purchase_date = date.fromisoformat(purchased_at)
    except ValueError as exc:
        raise StoreValidationError("purchased_at must use YYYY-MM-DD") from exc
    if purchase_date > date.today():
        raise StoreValidationError("purchased_at cannot be in the future")

    computed_sum_cents, reconciliation_status = _reconciliation(parsed, items)
    init_database(database_path)
    with connect(database_path) as connection, connection:
        # Serialize the idempotency check and insert. Without an immediate
        # write lock, concurrent scans of the same image can both miss and one
        # leaks a UNIQUE constraint failure instead of loading the winner.
        connection.execute("BEGIN IMMEDIATE")
        if image_hash is not None:
            existing = connection.execute(
                "SELECT id FROM receipts WHERE image_hash = ?", (image_hash,)
            ).fetchone()
            if existing is not None:
                rows = connection.execute(
                    "SELECT id FROM receipt_items WHERE receipt_id = ? ORDER BY id",
                    (existing["id"],),
                ).fetchall()
                return PersistDraftResult(
                    receipt_id=existing["id"],
                    item_ids=tuple(row["id"] for row in rows),
                    created=False,
                )

        cursor = connection.execute(
            """
            INSERT INTO receipts (
              store_name, purchased_at, image_hash, image_content_hash,
              subtotal_cents, tax_cents,
              total_cents, computed_sum_cents, reconciliation_status,
              overall_confidence, status, raw_model_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            """,
            (
                parsed.store_name,
                purchased_at,
                image_hash,
                image_content_hash,
                _to_cents(parsed.subtotal),
                _to_cents(parsed.tax),
                _to_cents(parsed.total),
                computed_sum_cents,
                reconciliation_status,
                parsed.overall_confidence,
                parsed.model_dump_json(),
            ),
        )
        receipt_id = int(cursor.lastrowid)
        item_ids: list[int] = []
        for item in items:
            excluded = bool(getattr(item, "excluded", item.category == "non_food"))
            item_cursor = connection.execute(
                """
                INSERT INTO receipt_items (
                  receipt_id, raw_text, name, canonical_key, qty, unit,
                  unit_price_cents, line_total_cents, category, is_perishable,
                  confidence, needs_review, excluded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item.raw_text,
                    item.name,
                    item.canonical_key,
                    item.qty,
                    item.unit,
                    _to_cents(item.unit_price) or 0,
                    _to_cents(item.line_total) or 0,
                    item.category,
                    int(item.is_perishable),
                    item.confidence,
                    int(item.needs_review),
                    int(excluded),
                ),
            )
            item_ids.append(int(item_cursor.lastrowid))

    return PersistDraftResult(receipt_id, tuple(item_ids), True)


def load_receipt_draft(
    receipt_id: int, *, database_path: str | Path | None = None
) -> ReceiptDraftRecord | None:
    init_database(database_path)
    with connect(database_path) as connection:
        receipt = connection.execute(
            "SELECT * FROM receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if receipt is None:
            return None
        item_rows = connection.execute(
            "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY id", (receipt_id,)
        ).fetchall()

    raw_parse: ReceiptParse | None = None
    if receipt["raw_model_json"]:
        try:
            raw_parse = ReceiptParse.model_validate(json.loads(receipt["raw_model_json"]))
        except (ValueError, TypeError):
            raw_parse = None
    return ReceiptDraftRecord(
        id=receipt["id"],
        store_name=receipt["store_name"],
        purchased_at=receipt["purchased_at"],
        image_hash=receipt["image_hash"],
        image_content_hash=receipt["image_content_hash"],
        subtotal_cents=receipt["subtotal_cents"],
        tax_cents=receipt["tax_cents"],
        total_cents=receipt["total_cents"],
        computed_sum_cents=receipt["computed_sum_cents"],
        reconciliation_status=receipt["reconciliation_status"],
        overall_confidence=receipt["overall_confidence"],
        status=receipt["status"],
        raw_parse=raw_parse,
        items=tuple(_row_to_receipt_item(row) for row in item_rows),
    )


def load_receipt_draft_by_image_hash(
    image_hash: str, *, database_path: str | Path | None = None
) -> ReceiptDraftRecord | None:
    init_database(database_path)
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT id FROM receipts WHERE image_hash = ?", (image_hash,)
        ).fetchone()
    if row is None:
        return None
    return load_receipt_draft(int(row["id"]), database_path=database_path)


def list_receipt_records(
    *, status: str = "confirmed", database_path: str | Path | None = None
) -> tuple[ReceiptDraftRecord, ...]:
    if status not in {"draft", "confirmed", "discarded"}:
        raise StoreValidationError("invalid receipt status")
    init_database(database_path)
    with connect(database_path) as connection:
        ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM receipts WHERE status = ? ORDER BY purchased_at DESC, id DESC",
                (status,),
            ).fetchall()
        ]
    records = [load_receipt_draft(receipt_id, database_path=database_path) for receipt_id in ids]
    return tuple(record for record in records if record is not None)


def confirm_receipt(
    receipt_id: int,
    resolved_items: Sequence[ReceiptDraftItem],
    *,
    store_name: str | None = None,
    purchased_at: str | None = None,
    database_path: str | Path | None = None,
) -> ConfirmResult:
    """Atomically apply reviewed items and create conservatively grounded pantry rows."""

    if not resolved_items:
        raise StoreValidationError("resolved_items cannot be empty")
    supplied_ids = [item.item_id for item in resolved_items]
    if len(supplied_ids) != len(set(supplied_ids)):
        raise StoreValidationError("resolved_items contains duplicate item IDs")

    init_database(database_path)
    with connect(database_path) as connection, connection:
        # Acquire the writer reservation before reading status. A second
        # concurrent confirmer waits, then observes `confirmed` and conflicts
        # instead of creating a duplicate pantry row from the same draft.
        connection.execute("BEGIN IMMEDIATE")
        receipt = connection.execute(
            "SELECT * FROM receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if receipt is None:
            raise RecordNotFoundError(f"receipt {receipt_id} was not found")
        if receipt["status"] != "draft":
            raise StoreConflictError(f"receipt {receipt_id} is already {receipt['status']}")

        stored_ids = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM receipt_items WHERE receipt_id = ?", (receipt_id,)
            ).fetchall()
        }
        if set(supplied_ids) != stored_ids:
            raise StoreValidationError(
                "resolved_items must contain every item belonging to the receipt"
            )

        final_purchase_date = purchased_at or receipt["purchased_at"]
        try:
            purchase_date = date.fromisoformat(final_purchase_date)
        except ValueError as exc:
            raise StoreValidationError("purchased_at must use YYYY-MM-DD") from exc
        if purchase_date > date.today():
            raise StoreValidationError("purchased_at cannot be in the future")

        pantry_item_ids: list[int] = []
        computed_sum_cents = 0
        for item in resolved_items:
            line_total_cents = _to_cents(item.line_total) or 0
            computed_sum_cents += line_total_cents
            connection.execute(
                """
                UPDATE receipt_items SET
                  raw_text = ?, name = ?, canonical_key = ?, qty = ?, unit = ?,
                  unit_price_cents = ?, line_total_cents = ?, category = ?,
                  is_perishable = ?, confidence = ?, needs_review = ?, excluded = ?
                WHERE id = ? AND receipt_id = ?
                """,
                (
                    item.raw_text,
                    item.name,
                    item.canonical_key,
                    item.qty,
                    item.unit,
                    _to_cents(item.unit_price) or 0,
                    line_total_cents,
                    item.category,
                    int(item.is_perishable),
                    item.confidence,
                    int(item.needs_review),
                    int(item.excluded),
                    item.item_id,
                    receipt_id,
                ),
            )

            if item.excluded or item.storage is None:
                continue
            if item.shelf_life_source is None:
                raise StoreValidationError(
                    f"item {item.item_id} has storage but no shelf_life_source"
                )
            duration_days = item.storage.duration_days
            if not 1 <= duration_days <= 730:
                raise StoreValidationError(
                    f"item {item.item_id} storage duration must be between 1 and 730"
                )
            eat_by_days = (
                item.eat_by_window.end_days
                if item.eat_by_window is not None
                else duration_days
            )
            eat_by_days = max(0, min(duration_days, eat_by_days))
            best_by = purchase_date + timedelta(days=eat_by_days)
            safe_until = purchase_date + timedelta(days=duration_days)
            pantry_cursor = connection.execute(
                """
                INSERT INTO pantry_items (
                  receipt_item_id, name, canonical_key, category, qty_initial,
                  qty_remaining, unit, unit_price_cents, storage_method,
                  storage_temp_c, storage_duration_days, shelf_life_source,
                  purchased_at, best_by, safe_until, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    item.item_id,
                    item.name,
                    item.canonical_key,
                    item.category,
                    item.qty,
                    item.qty,
                    item.unit,
                    _to_cents(item.unit_price) or 0,
                    item.storage.method,
                    item.storage.temp_c,
                    duration_days,
                    item.shelf_life_source,
                    final_purchase_date,
                    best_by.isoformat(),
                    safe_until.isoformat(),
                    _utc_now(),
                ),
            )
            pantry_item_ids.append(int(pantry_cursor.lastrowid))

        if receipt["subtotal_cents"] is not None:
            reconciliation_delta = computed_sum_cents - receipt["subtotal_cents"]
            reconciliation_status = (
                "ok" if abs(reconciliation_delta) <= 5 else "mismatch"
            )
        elif receipt["total_cents"] is not None:
            reconciliation_delta = (
                computed_sum_cents
                + (receipt["tax_cents"] or 0)
                - receipt["total_cents"]
            )
            reconciliation_status = (
                "ok" if abs(reconciliation_delta) <= 5 else "mismatch"
            )
        else:
            reconciliation_status = "unreadable"
        ledger_total_cents = (
            receipt["total_cents"]
            if receipt["total_cents"] is not None
            else computed_sum_cents
        )
        connection.execute(
            """
            UPDATE receipts SET
              store_name = ?, purchased_at = ?, computed_sum_cents = ?,
              reconciliation_status = ?, status = 'confirmed'
            WHERE id = ?
            """,
            (
                store_name if store_name is not None else receipt["store_name"],
                final_purchase_date,
                computed_sum_cents,
                reconciliation_status,
                receipt_id,
            ),
        )

    return ConfirmResult(receipt_id, tuple(pantry_item_ids), ledger_total_cents)


def list_pantry_items(
    *,
    status: str | None = "active",
    database_path: str | Path | None = None,
) -> tuple[PantryItemRecord, ...]:
    if status not in {None, "active", "eaten", "spoiled"}:
        raise StoreValidationError("invalid pantry status")
    init_database(database_path)
    query = "SELECT * FROM pantry_items"
    parameters: tuple[object, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY best_by, id"
    with connect(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return tuple(_row_to_pantry_item(row) for row in rows)


def consume_pantry_item(
    pantry_item_id: int,
    portion: float = 1.0,
    *,
    database_path: str | Path | None = None,
) -> PantryMutationResult:
    return _change_pantry_quantity(
        pantry_item_id, portion, spoiled=False, database_path=database_path
    )


def consume_pantry_items_batch(
    items: Sequence[tuple[int, float]],
    *,
    database_path: str | Path | None = None,
) -> tuple[PantryMutationResult, ...]:
    """Consume multiple pantry rows atomically after validating the full batch."""

    if not items:
        raise StoreValidationError("batch must contain at least one pantry item")
    pantry_item_ids = [pantry_item_id for pantry_item_id, _ in items]
    if len(pantry_item_ids) != len(set(pantry_item_ids)):
        raise StoreValidationError("batch contains duplicate pantry item IDs")
    for _, portion in items:
        if not 0 < portion <= 1:
            raise StoreValidationError("portion must be greater than 0 and at most 1")

    init_database(database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            validated: list[tuple[sqlite3.Row, float, float, str]] = []
            for pantry_item_id, portion in items:
                row = connection.execute(
                    "SELECT * FROM pantry_items WHERE id = ?", (pantry_item_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFoundError(
                        f"pantry item {pantry_item_id} was not found"
                    )
                if row["status"] != "active":
                    raise StoreConflictError(
                        f"pantry item {pantry_item_id} is {row['status']}"
                    )
                quantity = float(row["qty_initial"]) * portion
                remaining = float(row["qty_remaining"])
                if quantity > remaining + 1e-9:
                    raise StoreValidationError(
                        f"portion for pantry item {pantry_item_id} exceeds its remaining quantity"
                    )
                new_remaining = max(0.0, remaining - quantity)
                new_status = "eaten" if new_remaining <= 1e-9 else "active"
                validated.append((row, new_remaining, portion, new_status))

            now = _utc_now()
            results: list[PantryMutationResult] = []
            for row, new_remaining, _portion, new_status in validated:
                connection.execute(
                    """
                    UPDATE pantry_items
                    SET qty_remaining = ?, status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_remaining, new_status, now, row["id"]),
                )
                results.append(
                    PantryMutationResult(
                        pantry_item_id=int(row["id"]),
                        qty_remaining=new_remaining,
                        status=new_status,
                    )
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return tuple(results)


def spoil_pantry_item(
    pantry_item_id: int,
    portion: float = 1.0,
    *,
    database_path: str | Path | None = None,
) -> PantryMutationResult:
    return _change_pantry_quantity(
        pantry_item_id, portion, spoiled=True, database_path=database_path
    )


def waste_total_cents(*, database_path: str | Path | None = None) -> int:
    init_database(database_path)
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT COALESCE(SUM(cost_lost_cents), 0) AS total FROM waste_events"
        ).fetchone()
    return int(row["total"])


def reset_demo_data(*, database_path: str | Path | None = None) -> None:
    init_database(database_path)
    with connect(database_path) as connection, connection:
        for table in (
            "meal_suggestions",
            "insights_cache",
            "waste_events",
            "pantry_items",
            "receipt_items",
            "receipts",
        ):
            connection.execute(f"DELETE FROM {table}")


def _change_pantry_quantity(
    pantry_item_id: int,
    portion: float,
    *,
    spoiled: bool,
    database_path: str | Path | None,
) -> PantryMutationResult:
    if not 0 < portion <= 1:
        raise StoreValidationError("portion must be greater than 0 and at most 1")

    init_database(database_path)
    with connect(database_path) as connection, connection:
        row = connection.execute(
            "SELECT * FROM pantry_items WHERE id = ?", (pantry_item_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"pantry item {pantry_item_id} was not found")
        if row["status"] != "active":
            raise StoreConflictError(f"pantry item {pantry_item_id} is {row['status']}")

        quantity = float(row["qty_initial"]) * portion
        remaining = float(row["qty_remaining"])
        if quantity > remaining + 1e-9:
            raise StoreValidationError("portion exceeds the item's remaining quantity")
        new_remaining = max(0.0, remaining - quantity)
        new_status = ("spoiled" if spoiled else "eaten") if new_remaining <= 1e-9 else "active"
        now = _utc_now()
        connection.execute(
            "UPDATE pantry_items SET qty_remaining = ?, status = ?, updated_at = ? WHERE id = ?",
            (new_remaining, new_status, now, pantry_item_id),
        )

        waste_event_id: int | None = None
        cost_lost_cents: int | None = None
        if spoiled:
            cost_lost_cents = int(
                (Decimal(str(portion)) * Decimal(row["qty_initial"]) * row["unit_price_cents"])
                .quantize(Decimal("1"), ROUND_HALF_UP)
            )
            cursor = connection.execute(
                """
                INSERT INTO waste_events (pantry_item_id, portion, cost_lost_cents, occurred_at)
                VALUES (?, ?, ?, ?)
                """,
                (pantry_item_id, portion, cost_lost_cents, now),
            )
            waste_event_id = int(cursor.lastrowid)

    return PantryMutationResult(
        pantry_item_id=pantry_item_id,
        qty_remaining=new_remaining,
        status=new_status,
        waste_event_id=waste_event_id,
        cost_lost_cents=cost_lost_cents,
        occurred_at=now if spoiled else None,
    )
