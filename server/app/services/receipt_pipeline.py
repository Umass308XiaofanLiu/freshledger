from __future__ import annotations

from ..errors import AppError
from collections.abc import Sequence

from ..models import (
    ReceiptDraft,
    ReceiptLineItem,
    ReceiptParse,
    Reconciliation,
    ScanProvenance,
)
from .shelf_life import resolve_item


def reconcile(
    parsed: ReceiptParse, items: Sequence[ReceiptLineItem] | None = None
) -> Reconciliation:
    reconciled_items = items if items is not None else parsed.items
    computed = round(sum(item.line_total for item in reconciled_items), 2)
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

    return Reconciliation(
        printed_subtotal=printed_subtotal,
        printed_tax=printed_tax,
        printed_total=printed_total,
        computed_items_sum=computed,
        status=reconciliation_status,
        delta=delta,
    )


def retake_error(parsed: ReceiptParse) -> AppError:
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


def build_receipt_draft(
    parsed: ReceiptParse,
    *,
    receipt_id: int,
    item_ids: list[int],
    provenance: ScanProvenance,
) -> ReceiptDraft:
    if len(item_ids) != len(parsed.items):
        raise RuntimeError("Receipt item IDs did not match the parsed item count.")
    items = [
        resolve_item(item, item_id=item_id)
        for item, item_id in zip(parsed.items, item_ids, strict=True)
    ]
    return ReceiptDraft(
        receipt_id=receipt_id,
        scan_provenance=provenance,
        store_name=parsed.store_name,
        purchased_at=parsed.purchased_at,
        overall_confidence=parsed.overall_confidence,
        reconciliation=reconcile(parsed, items),
        items=items,
    )
