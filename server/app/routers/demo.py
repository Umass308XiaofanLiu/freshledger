from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request, status

from ..auth import require_admin_token, require_demo_token
from ..errors import AppError
from ..models import (
    DemoClearRequest,
    DemoClearResponse,
    DemoGeneration,
    DemoSeedRequest,
    DemoSeedResponse,
    ReceiptDraft,
    ScanProvenance,
)
from ..rate_limit import demo_admin_limit, limiter, standard_minute_limit
from ..services.demo_data import load_demo_parse, seed_demo_data
from ..services.receipt_pipeline import build_receipt_draft
from ..services.receipt_store import (
    StoreValidationError,
    persist_receipt_draft,
    reset_demo_data,
)
from ..services.shelf_life import resolve_item


router = APIRouter(
    prefix="/v1/demo",
    tags=["demo"],
    dependencies=[Depends(require_demo_token)],
)

@router.post(
    "/receipts/{sample_id}/scan",
    status_code=status.HTTP_201_CREATED,
    response_model=ReceiptDraft,
)
@limiter.limit(standard_minute_limit)
async def scan_demo_receipt(request: Request, sample_id: str) -> ReceiptDraft:
    parsed = load_demo_parse(sample_id)
    preliminary_items = [
        resolve_item(item, item_id=index)
        for index, item in enumerate(parsed.items, start=1)
    ]
    try:
        persisted = persist_receipt_draft(
            parsed,
            image_hash=None,
            resolved_items=preliminary_items,
            purchased_at_fallback=date.today().isoformat(),
        )
    except StoreValidationError as exc:
        raise AppError(
            422,
            "INVALID_SAMPLE",
            str(exc),
            "That saved receipt could not be prepared.",
        ) from exc

    return build_receipt_draft(
        parsed,
        receipt_id=persisted.receipt_id,
        item_ids=list(persisted.item_ids),
        provenance=ScanProvenance(
            mode="demo",
            ai_called=False,
            provider=None,
            model=None,
            fixture_id=sample_id,
        ),
    )


@router.post("/seed", response_model=DemoSeedResponse)
@limiter.limit(demo_admin_limit)
async def seed_demo(
    request: Request, payload: DemoSeedRequest
) -> DemoSeedResponse:
    del payload  # The strict literal profile reserves room for future seed sets.
    result = seed_demo_data()
    return DemoSeedResponse(
        receipts=result.receipts,
        pantry_items=result.pantry_items,
        waste_events=result.waste_events,
        generation=DemoGeneration(),
    )


@router.post("/clear", response_model=DemoClearResponse)
@limiter.limit(demo_admin_limit)
async def clear_user_data(
    request: Request, payload: DemoClearRequest
) -> DemoClearResponse:
    del payload  # Strict validation above is the destructive-action confirmation.
    return DemoClearResponse(
        deleted=reset_demo_data(
            include_product_aliases=True,
            suppress_auto_seed=True,
        )
    )


@router.post("/reset", dependencies=[Depends(require_admin_token)])
@limiter.limit(demo_admin_limit)
async def reset_demo(request: Request) -> dict[str, bool]:
    # Administrative reset restores the deployment's configured boot behavior.
    reset_demo_data(suppress_auto_seed=False)
    return {"reset": True}
