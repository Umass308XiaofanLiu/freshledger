from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_demo_token
from ..errors import AppError
from ..models import (
    DemoGeneration,
    MealConsumeRequest,
    MealConsumeResponse,
    MealConsumeResult,
    MealsTodayResponse,
)
from ..rate_limit import limiter, standard_minute_limit
from ..services.meals import get_meals_today
from ..services.receipt_store import (
    RecordNotFoundError,
    StoreConflictError,
    StoreValidationError,
    consume_pantry_items_batch,
)


router = APIRouter(
    prefix="/v1/meals",
    tags=["meals"],
    dependencies=[Depends(require_demo_token)],
)


@router.get("/today", response_model=MealsTodayResponse)
@limiter.limit(standard_minute_limit)
async def meals_today(
    request: Request,
    refresh: bool = Query(default=False),
) -> MealsTodayResponse:
    return get_meals_today(refresh=refresh)


@router.post("/consume", response_model=MealConsumeResponse)
@limiter.limit(standard_minute_limit)
async def consume_meal(
    request: Request, payload: MealConsumeRequest
) -> MealConsumeResponse:
    try:
        results = consume_pantry_items_batch(
            [(item.pantry_item_id, item.portion) for item in payload.items]
        )
    except RecordNotFoundError as exc:
        raise AppError(
            404,
            "NOT_FOUND",
            str(exc),
            "One of those fridge items could not be found, so nothing was changed.",
        ) from exc
    except StoreConflictError as exc:
        raise AppError(
            409,
            "ITEM_ALREADY_FINISHED",
            str(exc),
            "One of those items has already left your fridge, so nothing was changed.",
        ) from exc
    except StoreValidationError as exc:
        raise AppError(
            422,
            "INVALID_MEAL_PORTION",
            str(exc),
            "Those meal amounts do not match what remains, so nothing was changed.",
        ) from exc

    return MealConsumeResponse(
        consumed=[
            MealConsumeResult(
                pantry_item_id=result.pantry_item_id,
                qty_remaining=result.qty_remaining,
                status=result.status,  # type: ignore[arg-type]
            )
            for result in results
        ],
        generation=DemoGeneration(),
    )
