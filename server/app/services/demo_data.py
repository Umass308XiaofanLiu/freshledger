from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from ..db import connect, init_database
from ..errors import AppError
from ..models import ReceiptParse
from .meals import get_meals_today
from .receipt_store import (
    confirm_receipt,
    persist_receipt_draft,
    reset_demo_data,
)
from .shelf_life import resolve_item


FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "receipts" / "expected"
SAMPLE_IDS = ("r1", "r2", "r3")


@dataclass(frozen=True)
class DemoSeedResult:
    receipts: int
    pantry_items: int
    waste_events: int


def load_demo_parse(sample_id: str) -> ReceiptParse:
    if sample_id not in SAMPLE_IDS:
        raise AppError(
            404,
            "SAMPLE_NOT_FOUND",
            f"Demo sample {sample_id!r} is not in the allowlist.",
            "That saved receipt sample could not be found.",
        )
    path = FIXTURE_ROOT / f"{sample_id}.json"
    try:
        return ReceiptParse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise AppError(
            500,
            "SAMPLE_MISSING",
            f"The bundled fixture {path.name} is missing.",
            "That saved receipt is temporarily unavailable.",
        ) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AppError(
            500,
            "SAMPLE_INVALID",
            f"The bundled fixture {path.name} failed strict validation: {exc}",
            "That saved receipt is temporarily unavailable.",
        ) from exc


def database_is_empty(*, database_path: str | Path | None = None) -> bool:
    init_database(database_path)
    with connect(database_path) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM receipts").fetchone()
    return int(row["count"]) == 0


def seed_demo_data(*, database_path: str | Path | None = None) -> DemoSeedResult:
    """Rebuild the deterministic judge profile without making an AI call."""

    parses = [(sample_id, load_demo_parse(sample_id)) for sample_id in SAMPLE_IDS]
    # Resolve every fixture before changing persisted state. This catches a broken
    # shelf-life reference or fixture contract while the existing DB is intact.
    for _sample_id, parsed in parses:
        for index, item in enumerate(parsed.items, start=1):
            resolve_item(item, item_id=index)

    reset_demo_data(database_path=database_path)
    pantry_items = 0
    try:
        for sample_id, parsed in parses:
            preliminary = [
                resolve_item(item, item_id=index)
                for index, item in enumerate(parsed.items, start=1)
            ]
            persisted = persist_receipt_draft(
                parsed,
                image_hash=f"demo-seed:{sample_id}",
                resolved_items=preliminary,
                purchased_at_fallback=date.today().isoformat(),
                database_path=database_path,
            )
            resolved = [
                resolve_item(item, item_id=item_id)
                for item, item_id in zip(
                    parsed.items, persisted.item_ids, strict=True
                )
            ]
            confirmed = confirm_receipt(
                persisted.receipt_id,
                resolved,
                store_name=parsed.store_name,
                purchased_at=parsed.purchased_at,
                database_path=database_path,
            )
            pantry_items += len(confirmed.pantry_item_ids)

        # The cache is deterministic and zero-token; warming it keeps the first
        # judge-facing Meals visit instant without putting AI on the boot path.
        get_meals_today(database_path=database_path)
    except Exception:
        # Never leave an apparently initialized, half-seeded database. Startup
        # can safely retry while the receipts table remains empty.
        reset_demo_data(database_path=database_path)
        raise

    with connect(database_path) as connection:
        waste_row = connection.execute(
            "SELECT COUNT(*) AS count FROM waste_events"
        ).fetchone()
    return DemoSeedResult(
        receipts=len(parses),
        pantry_items=pantry_items,
        waste_events=int(waste_row["count"]),
    )
