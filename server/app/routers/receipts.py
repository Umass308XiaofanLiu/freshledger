from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Depends, File, Request, UploadFile, status

from ..auth import require_demo_token
from ..config import get_settings
from ..errors import AppError
from ..models import (
    ConfirmReceiptRequest,
    ConfirmReceiptResponse,
    ReceiptDraft,
    ReceiptLineItem,
    ScanProvenance,
)
from ..rate_limit import limiter, scan_daily_limit, scan_minute_limit, standard_minute_limit
from ..services.images import MAX_UPLOAD_BYTES, prepare_receipt_image
from ..services.receipt_pipeline import build_receipt_draft, retake_error
from ..services.receipt_store import (
    RecordNotFoundError,
    StoreConflictError,
    StoreValidationError,
    confirm_receipt as confirm_receipt_in_store,
    load_receipt_draft,
    load_receipt_draft_by_image_hash,
    list_receipt_records,
    persist_receipt_draft,
)
from ..services.shelf_life import resolve_item
from ..services.vision import parse_receipt_image


router = APIRouter(
    prefix="/v1/receipts",
    tags=["receipts"],
    dependencies=[Depends(require_demo_token)],
)


def _store_error(exc: Exception) -> AppError:
    if isinstance(exc, RecordNotFoundError):
        return AppError(404, "NOT_FOUND", str(exc), "That receipt could not be found.")
    if isinstance(exc, StoreConflictError):
        return AppError(
            409,
            "RECEIPT_CONFLICT",
            str(exc),
            "That receipt has already been confirmed.",
        )
    return AppError(
        422,
        "INVALID_RECEIPT",
        str(exc),
        "Some receipt details were invalid — check them and try again.",
    )


def _live_provenance() -> ScanProvenance:
    return ScanProvenance(
        mode="live",
        ai_called=True,
        provider="openai",
        model=get_settings().openai_model,
        fixture_id=None,
    )


@router.get("")
@limiter.limit(standard_minute_limit)
async def list_receipts(request: Request) -> dict[str, object]:
    records = list_receipt_records(status="confirmed")
    receipts = []
    total_cents = 0
    for record in records:
        receipt_total = (
            record.total_cents
            if record.total_cents is not None
            else record.computed_sum_cents
        )
        total_cents += receipt_total
        receipts.append(
            {
                "receipt_id": record.id,
                "store_name": record.store_name,
                "purchased_at": record.purchased_at,
                "total": receipt_total / 100,
                "item_count": len(record.items),
                "items": [
                    {
                        "name": item.name,
                        "line_total": item.line_total_cents / 100,
                        "category": item.category,
                        "excluded": item.excluded,
                    }
                    for item in record.items
                ],
            }
        )
    return {
        "receipts": receipts,
        "summary": {
            "total_spent": total_cents / 100,
            "receipt_count": len(records),
            "since": min((record.purchased_at for record in records), default=None),
        },
    }


@router.post("/scan", status_code=status.HTTP_201_CREATED, response_model=ReceiptDraft)
@limiter.limit(scan_daily_limit)
@limiter.limit(scan_minute_limit)
async def scan_receipt(
    request: Request, image: UploadFile = File(...)
) -> ReceiptDraft:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES + 1_000_000:
                raise AppError(
                    413,
                    "IMAGE_TOO_LARGE",
                    "The multipart request exceeded the upload limit.",
                    "That photo is too large — choose one under 8 MB.",
                )
        except ValueError:
            pass

    raw_bytes = await image.read(MAX_UPLOAD_BYTES + 1)
    image_hash = hashlib.sha256(raw_bytes).hexdigest()

    existing = load_receipt_draft_by_image_hash(image_hash)
    if existing is not None and existing.raw_parse is not None:
        return build_receipt_draft(
            existing.raw_parse,
            receipt_id=existing.id,
            item_ids=[item.id for item in existing.items],
            provenance=_live_provenance(),
        )

    jpeg_bytes = prepare_receipt_image(raw_bytes)
    parsed = await parse_receipt_image(jpeg_bytes)
    if parsed.image_quality_issue is not None and parsed.overall_confidence < 0.5:
        raise retake_error(parsed)

    preliminary_items = [
        resolve_item(item, item_id=index)
        for index, item in enumerate(parsed.items, start=1)
    ]
    try:
        persisted = persist_receipt_draft(
            parsed,
            image_hash=image_hash,
            resolved_items=preliminary_items,
            purchased_at_fallback=date.today().isoformat(),
        )
    except (StoreValidationError, StoreConflictError) as exc:
        raise _store_error(exc) from exc

    return build_receipt_draft(
        parsed,
        receipt_id=persisted.receipt_id,
        item_ids=list(persisted.item_ids),
        provenance=_live_provenance(),
    )


@router.post(
    "/{receipt_id}/confirm",
    response_model=ConfirmReceiptResponse,
)
@limiter.limit(standard_minute_limit)
async def confirm_receipt(
    request: Request, receipt_id: int, payload: ConfirmReceiptRequest
) -> ConfirmReceiptResponse:
    record = load_receipt_draft(receipt_id)
    if record is None or record.raw_parse is None:
        raise AppError(
            404,
            "NOT_FOUND",
            f"Receipt {receipt_id} was not found or has no stored parse.",
            "That receipt could not be found.",
        )

    edits = {item.item_id: item for item in payload.items}
    resolved_items = []
    for original, stored in zip(record.raw_parse.items, record.items, strict=True):
        edit = edits.get(stored.id)
        if edit is None:
            raise AppError(
                422,
                "MISSING_ITEM",
                f"Receipt item {stored.id} was omitted from confirmation.",
                "Review every receipt item before confirming.",
            )
        updates: dict[str, object] = {}
        for field in ("name", "qty", "unit", "unit_price", "category"):
            value = getattr(edit, field)
            if value is not None:
                updates[field] = value
        if "name" in updates:
            updates["canonical_key"] = None
        if updates.get("category") == "non_food":
            updates["is_perishable"] = False
            updates["storage"] = None
            updates["eat_by_window"] = None
        edited: ReceiptLineItem = original.model_copy(update=updates)
        excluded = edit.excluded if edit.excluded is not None else stored.excluded
        resolved_items.append(
            resolve_item(
                edited,
                item_id=stored.id,
                excluded=excluded,
                method_override=edit.storage_method_override,
            )
        )

    try:
        result = confirm_receipt_in_store(
            receipt_id,
            resolved_items,
            store_name=payload.store_name,
            purchased_at=payload.purchased_at,
        )
    except (RecordNotFoundError, StoreConflictError, StoreValidationError) as exc:
        raise _store_error(exc) from exc

    pantry_records = []
    if result.pantry_item_ids:
        from ..services.receipt_store import list_pantry_items

        ids = set(result.pantry_item_ids)
        pantry_records = [item for item in list_pantry_items() if item.id in ids]
    today = date.today()
    expiring_soon = [
        {
            "pantry_item_id": item.id,
            "name": item.name,
            "days_left": (date.fromisoformat(item.best_by) - today).days,
        }
        for item in pantry_records
        if (date.fromisoformat(item.best_by) - today).days <= 3
    ]
    ledger_cents = record.total_cents if record.total_cents is not None else record.computed_sum_cents
    return ConfirmReceiptResponse(
        receipt_id=receipt_id,
        pantry_items_created=len(result.pantry_item_ids),
        ledger_total=ledger_cents / 100,
        expiring_soon=expiring_soon,
    )
