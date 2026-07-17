from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, UploadFile, status

from ..auth import require_demo_token
from ..errors import AppError
from ..models import ReceiptParse
from ..services.images import MAX_UPLOAD_BYTES, prepare_receipt_image
from ..services.vision import parse_receipt_image


router = APIRouter(
    prefix="/v1/receipts",
    tags=["receipts"],
    dependencies=[Depends(require_demo_token)],
)


def _reconcile(parsed: ReceiptParse) -> dict[str, float | str | None]:
    computed = round(sum(item.line_total for item in parsed.items), 2)
    printed_subtotal = parsed.subtotal
    printed_tax = parsed.tax
    printed_total = parsed.total

    if printed_subtotal is not None:
        delta = round(computed - printed_subtotal, 2)
        reconciliation_status = "ok" if abs(delta) <= 0.05 else "mismatch"
    elif printed_total is not None:
        expected_total = round(computed + (printed_tax or 0.0), 2)
        delta = round(expected_total - printed_total, 2)
        reconciliation_status = "ok" if abs(delta) <= 0.05 else "mismatch"
    else:
        delta = None
        reconciliation_status = "unreadable"

    return {
        "printed_subtotal": printed_subtotal,
        "printed_tax": printed_tax,
        "printed_total": printed_total,
        "computed_items_sum": computed,
        "status": reconciliation_status,
        "delta": delta,
    }


def _retake_error(parsed: ReceiptParse) -> AppError:
    copy = {
        "blurry": "Hold steady and get closer, then try again.",
        "dark": "Try more light, then scan the receipt again.",
        "cropped": "Flatten the receipt and fit the whole thing in frame.",
        "glare": "Move away from glare and try the photo again.",
        "not_a_receipt": "That doesn't look like a receipt 🙂",
    }
    issue = parsed.image_quality_issue or "unreadable"
    return AppError(
        422,
        "RETAKE_PHOTO",
        f"The receipt image failed the quality gate: {issue}.",
        copy.get(issue, "Try another receipt photo with the full page in focus."),
    )


@router.post("/scan", status_code=status.HTTP_201_CREATED)
async def scan_receipt(image: UploadFile = File(...)) -> dict[str, object]:
    raw_bytes = await image.read(MAX_UPLOAD_BYTES + 1)
    jpeg_bytes = prepare_receipt_image(raw_bytes)
    parsed = await parse_receipt_image(jpeg_bytes)

    if parsed.image_quality_issue is not None and parsed.overall_confidence < 0.5:
        raise _retake_error(parsed)

    image_hash = hashlib.sha256(raw_bytes).hexdigest()
    items: list[dict[str, object]] = []
    for index, item in enumerate(parsed.items, start=1):
        item_payload = item.model_dump(mode="json")
        item_payload.update(
            {
                "item_id": index,
                "excluded": item.category == "non_food",
                "storage_options": None,
                "shelf_life_source": "llm_unverified" if item.storage else None,
            }
        )
        items.append(item_payload)

    return {
        "receipt_id": int(image_hash[:8], 16),
        "status": "draft",
        "store_name": parsed.store_name,
        "purchased_at": parsed.purchased_at,
        "overall_confidence": parsed.overall_confidence,
        "reconciliation": _reconcile(parsed),
        "items": items,
    }

