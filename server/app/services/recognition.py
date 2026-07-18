from __future__ import annotations

import inspect
from typing import Literal

from ..config import Settings, get_settings
from ..models import ReceiptParse, ScanProvenance
from .receipt_store import find_product_alias
from .shelf_life import load_reference_rows
from .vision import parse_receipt_image as parse_openai_receipt_image


LOCAL_PROVIDER = "rapidocr"
LOCAL_MODEL = "PP-OCRv6-small"
RECOGNITION_PIPELINE_VERSION = "receipt-parser-v2-matcher-v2"
RecognitionEngine = Literal["offline", "openai"]


def _selected_engine(
    settings: Settings, engine: RecognitionEngine | None = None
) -> RecognitionEngine:
    return engine or settings.receipt_scan_engine


def recognition_provenance(
    settings: Settings | None = None,
    *,
    engine: RecognitionEngine | None = None,
) -> ScanProvenance:
    """Describe the configured real-receipt recognizer without invoking it."""

    configured = settings or get_settings()
    if _selected_engine(configured, engine) == "offline":
        return ScanProvenance(
            mode="live",
            ai_called=False,
            provider=LOCAL_PROVIDER,
            model=LOCAL_MODEL,
            fixture_id=None,
        )
    return ScanProvenance(
        mode="live",
        ai_called=True,
        provider="openai",
        model=configured.openai_model,
        fixture_id=None,
    )


def recognition_cache_key(
    content_hash: str,
    settings: Settings | None = None,
    *,
    engine: RecognitionEngine | None = None,
) -> str:
    """Namespace a raw content digest by recognizer and model version."""

    provenance = recognition_provenance(settings, engine=engine)
    return (
        f"{provenance.provider}:{provenance.model}:"
        f"{RECOGNITION_PIPELINE_VERSION}:{content_hash}"
    )


def apply_confirmed_aliases(parsed: ReceiptParse) -> ReceiptParse:
    """Apply only exact, user-confirmed merchant/raw-line mappings."""

    updated_items = []
    changed = False
    for item in parsed.items:
        alias = find_product_alias(item.raw_text, merchant_name=parsed.store_name)
        if alias is None:
            alias = find_product_alias(item.name, merchant_name=parsed.store_name)
        if alias is None:
            updated_items.append(item)
            continue
        reference = next(
            (
                row
                for row in load_reference_rows()
                if row.canonical_key == alias.canonical_key
            ),
            None,
        )
        if reference is None:
            updated_items.append(item)
            continue
        is_perishable = (
            reference.category not in {"pantry_staple", "non_food"}
            and (
                reference.recommended_method in {"fridge", "freezer"}
                or reference.category
                in {"bakery", "dairy", "deli", "meat", "produce", "seafood"}
            )
        )
        changed = True
        updated_items.append(
            item.model_copy(
                update={
                    "name": alias.display_name,
                    "canonical_key": alias.canonical_key,
                    "category": reference.category,
                    "is_perishable": is_perishable,
                }
            )
        )
    if not changed:
        return parsed
    return parsed.model_copy(update={"items": updated_items})


async def recognize_receipt_image(
    jpeg_bytes: bytes, *, engine: RecognitionEngine | None = None
) -> ReceiptParse:
    """Run the configured recognizer behind the stable receipt-scan contract."""

    settings = get_settings()
    if _selected_engine(settings, engine) == "openai":
        parsed = await parse_openai_receipt_image(jpeg_bytes)
    else:
        # Imported lazily so Demo Mode and the optional OpenAI path do not load
        # OCR models or native runtime dependencies.
        from .local_ocr import parse_local_receipt_image

        result = parse_local_receipt_image(jpeg_bytes)
        parsed = await result if inspect.isawaitable(result) else result
    return apply_confirmed_aliases(parsed)
