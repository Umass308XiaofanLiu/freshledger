from __future__ import annotations

import hashlib
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile, status

from ..auth import require_demo_token
from ..errors import AppError
from ..models import (
    ConfirmReceiptRequest,
    ConfirmReceiptResponse,
    ReceiptDraft,
    ReceiptDraftItem,
    ReceiptLineItem,
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
    normalize_product_alias_key,
    persist_receipt_draft,
    upsert_product_alias,
)
from ..services.product_matcher import match_product
from ..services.recognition import (
    apply_confirmed_aliases,
    recognition_cache_key,
    recognition_provenance,
    recognize_receipt_image as parse_receipt_image,
)
from ..services.shelf_life import resolve_item


router = APIRouter(
    prefix="/v1/receipts",
    tags=["receipts"],
    dependencies=[Depends(require_demo_token)],
)
logger = logging.getLogger(__name__)


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
    request: Request,
    image: UploadFile = File(...),
    engine: Literal["offline"] | None = None,
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
    image_content_hash = hashlib.sha256(raw_bytes).hexdigest()
    image_hash = recognition_cache_key(image_content_hash, engine=engine)

    existing = load_receipt_draft_by_image_hash(image_hash)
    if existing is not None and existing.raw_parse is not None:
        if existing.status != "draft":
            raise AppError(
                409,
                "RECEIPT_ALREADY_IMPORTED",
                f"Receipt image is already stored as receipt {existing.id}.",
                "This receipt is already in your ledger.",
            )
        cached_parse = apply_confirmed_aliases(existing.raw_parse)
        return build_receipt_draft(
            cached_parse,
            receipt_id=existing.id,
            item_ids=[item.id for item in existing.items],
            provenance=recognition_provenance(engine=engine),
        )

    jpeg_bytes = prepare_receipt_image(raw_bytes)
    parsed = (
        await parse_receipt_image(jpeg_bytes)
        if engine is None
        else await parse_receipt_image(jpeg_bytes, engine=engine)
    )
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
            image_content_hash=image_content_hash,
            resolved_items=preliminary_items,
            purchased_at_fallback=date.today().isoformat(),
        )
    except (StoreValidationError, StoreConflictError) as exc:
        raise _store_error(exc) from exc

    if not persisted.created:
        # Another request won the same-image insert after our initial cache
        # check. Return the winner's stored parse and IDs, never this request's
        # potentially divergent recognition result.
        winner = load_receipt_draft(persisted.receipt_id)
        if winner is None or winner.raw_parse is None:
            raise AppError(
                409,
                "RECEIPT_ALREADY_IMPORTED",
                "The receipt was imported concurrently but could not be reloaded.",
                "This receipt is already being processed; reopen it from your ledger.",
            )
        if winner.status != "draft":
            raise AppError(
                409,
                "RECEIPT_ALREADY_IMPORTED",
                f"Receipt image is already stored as receipt {winner.id}.",
                "This receipt is already in your ledger.",
            )
        return build_receipt_draft(
            apply_confirmed_aliases(winner.raw_parse),
            receipt_id=winner.id,
            item_ids=[item.id for item in winner.items],
            provenance=recognition_provenance(engine=engine),
        )

    return build_receipt_draft(
        parsed,
        receipt_id=persisted.receipt_id,
        item_ids=list(persisted.item_ids),
        provenance=recognition_provenance(engine=engine),
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

    payload_ids = [item.item_id for item in payload.items]
    if len(payload_ids) != len(set(payload_ids)):
        raise AppError(
            422,
            "DUPLICATE_ITEM",
            "The confirmation payload contained a duplicate receipt item ID.",
            "Review each receipt item exactly once before confirming.",
        )
    stored_ids = {item.id for item in record.items}
    missing_ids = stored_ids - set(payload_ids)
    if missing_ids:
        missing_id = min(missing_ids)
        raise AppError(
            422,
            "MISSING_ITEM",
            f"Receipt item {missing_id} was omitted from confirmation.",
            "Review every receipt item before confirming.",
        )
    extra_ids = set(payload_ids) - stored_ids
    if extra_ids:
        raise AppError(
            422,
            "UNKNOWN_ITEM",
            f"Receipt item {min(extra_ids)} does not belong to receipt {receipt_id}.",
            "Refresh the receipt review and try again.",
        )

    edits = {item.item_id: item for item in payload.items}
    resolved_items = []
    alias_learnings: list[tuple[str, ReceiptDraftItem]] = []
    for original, stored in zip(record.raw_parse.items, record.items, strict=True):
        edit = edits.get(stored.id)
        if edit is None:  # exact ID-set validation above makes this unreachable
            raise RuntimeError("validated receipt item edit was missing")
        if edit.name is not None and not edit.name.strip():
            raise AppError(
                422,
                "INVALID_RECEIPT",
                f"Receipt item {stored.id} had an empty corrected name.",
                "Give each receipt item a name before confirming.",
            )
        explicit_name_change = (
            edit.name is not None
            and normalize_product_alias_key(edit.name)
            != normalize_product_alias_key(stored.name)
        )
        explicit_category_change = (
            edit.category is not None and edit.category != stored.category
        )
        grounded_name_change = False
        updates: dict[str, object] = {}
        for field in ("name", "qty", "unit", "unit_price", "category"):
            value = getattr(edit, field)
            if value is not None:
                updates[field] = value.strip() if field == "name" else value
        if "qty" in updates or "unit_price" in updates:
            corrected_qty = Decimal(str(updates.get("qty", original.qty)))
            corrected_unit_price = Decimal(
                str(updates.get("unit_price", original.unit_price))
            )
            corrected_line_total = (corrected_qty * corrected_unit_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if not Decimal("-500") <= corrected_line_total <= Decimal("500"):
                raise AppError(
                    422,
                    "INVALID_RECEIPT",
                    "The corrected item total exceeded the supported range.",
                    "Check the item quantity and price, then try again.",
                )
            updates["line_total"] = float(corrected_line_total)
        if explicit_name_change:
            updates["canonical_key"] = None
            exact_match = match_product(
                str(updates["name"]), ocr_confidence=1.0
            )
            if (
                exact_match.method == "exact"
                and exact_match.canonical_key is not None
                and not (
                    explicit_category_change
                    and edit.category != exact_match.category
                )
            ):
                grounded_name_change = True
                updates.update(
                    canonical_key=exact_match.canonical_key,
                    category=exact_match.category,
                    is_perishable=exact_match.is_perishable,
                    storage=None,
                    eat_by_window=None,
                )
            else:
                # A changed identity cannot inherit safety attributes from
                # the OCR guess it replaced. Abstain and let resolve_item
                # apply the conservative unknown-food default instead.
                updates.update(
                    canonical_key=None,
                    category="unknown",
                    is_perishable=True,
                    storage=None,
                    eat_by_window=None,
                    confidence=min(original.confidence, 0.5),
                    needs_review=True,
                )
        if explicit_category_change and not grounded_name_change:
            # Preserve the user's explicit category even when an incompatible
            # simultaneous name correction forced the identity to abstain.
            selected_category = str(edit.category)
            updates.update(
                canonical_key=None,
                category=selected_category,
                is_perishable=selected_category != "non_food",
                storage=None,
                eat_by_window=None,
                needs_review=selected_category != "non_food",
            )
        if updates.get("category") == "non_food":
            updates["is_perishable"] = False
            updates["storage"] = None
            updates["eat_by_window"] = None
        edited: ReceiptLineItem = original.model_copy(update=updates)
        excluded = edit.excluded if edit.excluded is not None else stored.excluded
        resolved = resolve_item(
            edited,
            item_id=stored.id,
            excluded=excluded,
            method_override=(
                None
                if explicit_name_change or explicit_category_change
                else edit.storage_method_override
            ),
            allow_reference=not (
                (explicit_name_change or explicit_category_change)
                and not grounded_name_change
            ),
        )
        resolved_items.append(resolved)
        # Supplying every form field is not a correction. Learn only when the
        # user explicitly changed the product name and it grounded to a trusted
        # shelf-life canonical key.
        if (
            explicit_name_change
            and grounded_name_change
            and resolved.canonical_key is not None
            and not resolved.excluded
        ):
            alias_learnings.append((stored.name, resolved))

    try:
        result = confirm_receipt_in_store(
            receipt_id,
            resolved_items,
            store_name=payload.store_name,
            purchased_at=payload.purchased_at,
        )
    except (RecordNotFoundError, StoreConflictError, StoreValidationError) as exc:
        raise _store_error(exc) from exc

    merchant_name = (
        payload.store_name if payload.store_name is not None else record.store_name
    )
    for raw_text, learned in alias_learnings:
        if not merchant_name:
            continue
        if learned.canonical_key is None:  # guarded when the learning is queued
            continue
        try:
            upsert_product_alias(
                raw_text,
                canonical_key=learned.canonical_key,
                display_name=learned.name,
                category=learned.category,
                is_perishable=learned.is_perishable,
                merchant_name=merchant_name,
            )
        except Exception:  # learning is best-effort after the atomic confirmation
            logger.warning("Could not persist a confirmed product alias.", exc_info=True)

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
    return ConfirmReceiptResponse(
        receipt_id=receipt_id,
        pantry_items_created=len(result.pantry_item_ids),
        ledger_total=result.ledger_total_cents / 100,
        expiring_soon=expiring_soon,
    )
