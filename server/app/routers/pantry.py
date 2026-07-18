from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_demo_token
from ..errors import AppError
from ..models import (
    FreezeRescue,
    PantryItemResponse,
    PantryResponse,
    PantryStorage,
    PortionRequest,
)
from ..rate_limit import limiter, standard_minute_limit
from ..services.receipt_store import (
    RecordNotFoundError,
    StoreConflictError,
    StoreValidationError,
    consume_pantry_item,
    list_pantry_items,
    spoil_pantry_item,
    waste_total_cents,
)
from ..services.shelf_life import load_reference_rows


router = APIRouter(
    prefix="/v1/pantry",
    tags=["pantry"],
    dependencies=[Depends(require_demo_token)],
)


def _mutation_error(exc: Exception) -> AppError:
    if isinstance(exc, RecordNotFoundError):
        return AppError(404, "NOT_FOUND", str(exc), "That fridge item could not be found.")
    if isinstance(exc, StoreConflictError):
        return AppError(
            409,
            "ITEM_ALREADY_FINISHED",
            str(exc),
            "That item has already left your fridge.",
        )
    return AppError(
        422,
        "INVALID_PORTION",
        str(exc),
        "That amount does not match what remains in your fridge.",
    )


def _freshness(days_left: int) -> str:
    if days_left < 0:
        return "expired"
    if days_left <= 1:
        return "urgent"
    if days_left <= 3:
        return "soon"
    return "fresh"


@router.get("", response_model=PantryResponse)
@limiter.limit(standard_minute_limit)
async def get_pantry(
    request: Request,
    status: str = Query(default="active", pattern="^(active|eaten|spoiled)$"),
) -> PantryResponse:
    try:
        records = list_pantry_items(status=status)
    except StoreValidationError as exc:
        raise _mutation_error(exc) from exc

    today = date.today()
    reference_by_key = {row.canonical_key: row for row in load_reference_rows()}
    items: list[PantryItemResponse] = []
    counts = {"expired": 0, "urgent": 0, "soon": 0, "fresh": 0}
    value_cents = Decimal(0)
    for record in records:
        days_left = (date.fromisoformat(record.best_by) - today).days
        freshness = _freshness(days_left)
        counts[freshness] += 1
        reference = reference_by_key.get(record.canonical_key or "")
        freezer_days = reference.freezer_days if reference is not None else None
        value_cents += (
            Decimal(str(record.qty_remaining)) * record.unit_price_cents
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        items.append(
            PantryItemResponse(
                pantry_item_id=record.id,
                name=record.name,
                canonical_key=record.canonical_key,
                category=record.category,
                qty_initial=record.qty_initial,
                qty_remaining=record.qty_remaining,
                unit=record.unit,
                unit_price=record.unit_price_cents / 100,
                storage=PantryStorage(
                    method=record.storage_method,  # type: ignore[arg-type]
                    temp_c=record.storage_temp_c,
                    duration_days=record.storage_duration_days,
                ),
                purchased_at=record.purchased_at,
                best_by=record.best_by,
                safe_until=record.safe_until,
                days_left=days_left,
                freshness=freshness,  # type: ignore[arg-type]
                freeze_rescue=FreezeRescue(
                    possible=record.storage_method != "freezer" and freezer_days is not None,
                    freezer_days=freezer_days,
                ),
            )
        )

    return PantryResponse(
        items=items,
        counts=counts,
        value_in_stock=float(
            (value_cents / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
    )


@router.post("/{pantry_item_id}/consume")
@limiter.limit(standard_minute_limit)
async def consume_item(
    request: Request, pantry_item_id: int, payload: PortionRequest
) -> dict[str, object]:
    try:
        result = consume_pantry_item(pantry_item_id, payload.portion)
    except (RecordNotFoundError, StoreConflictError, StoreValidationError) as exc:
        raise _mutation_error(exc) from exc
    return {"qty_remaining": result.qty_remaining, "status": result.status}


@router.post("/{pantry_item_id}/spoil")
@limiter.limit(standard_minute_limit)
async def spoil_item(
    request: Request, pantry_item_id: int, payload: PortionRequest
) -> dict[str, object]:
    try:
        result = spoil_pantry_item(pantry_item_id, payload.portion)
    except (RecordNotFoundError, StoreConflictError, StoreValidationError) as exc:
        raise _mutation_error(exc) from exc
    return {
        "status": result.status,
        "qty_remaining": result.qty_remaining,
        "waste_event": {
            "id": result.waste_event_id,
            "cost_lost": (result.cost_lost_cents or 0) / 100,
            "occurred_at": result.occurred_at,
        },
        "waste_total_to_date": waste_total_cents() / 100,
    }
