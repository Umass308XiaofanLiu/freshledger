from __future__ import annotations

import asyncio
import math
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from typing import Any, Sequence

from ..errors import AppError
from ..models import ReceiptLineItem, ReceiptParse
from .product_matcher import match_product


Point = tuple[float, float]
Box = tuple[Point, ...]


@dataclass(frozen=True)
class OcrToken:
    text: str
    box: Box
    confidence: float

    @property
    def left(self) -> float:
        return min(point[0] for point in self.box)

    @property
    def right(self) -> float:
        return max(point[0] for point in self.box)

    @property
    def top(self) -> float:
        return min(point[1] for point in self.box)

    @property
    def bottom(self) -> float:
        return max(point[1] for point in self.box)

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: Box
    confidence: float
    tokens: tuple[OcrToken, ...]

    @property
    def top(self) -> float:
        return min(point[1] for point in self.box)

    @property
    def left(self) -> float:
        return min(point[0] for point in self.box)


@dataclass(frozen=True)
class _MoneyMatch:
    value: Decimal
    start: int
    end: int


_MONEY_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<prefix>[-\u2212(]?\s*[$:]?\s*)"
    r"(?P<number>\d{1,6}(?:,\d{3})*\.\d{2})"
    r"(?P<suffix>\)?\s*-?)"
    r"(?!\d)"
)

_AT_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>lb|lbs|oz|kg|g)?\s*@\s*[$:]?\s*"
    r"(?P<price>\d{1,6}(?:,\d{3})*\.\d{2})"
    r"(?:\s*(?:/|per)?\s*(?P<price_unit>lb|lbs|oz|kg|g|each))?",
    re.IGNORECASE,
)

_PACKAGE_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>fl\s*oz|oz|lbs?|kg|g|gallons?|gal|liters?|ltr|l|"
    r"packs?|pk|counts?|ct|dozens?|doz)\s*$",
    re.IGNORECASE,
)

_QUANTITY_SUFFIX_RE = re.compile(
    r"(?:\s*[xX]\s*(?P<qty>\d+(?:\.\d+)?))\s*$"
)

_QUANTITY_PREFIX_RE = re.compile(
    r"^\s*(?P<qty>\d+(?:\.\d+)?)\s*[xX]\s+(?P<name>.+)$"
)

_DISCOUNT_RE = re.compile(
    r"\b(coupon|discount|markdown|promotion|promo|saved|savings?)\b",
    re.IGNORECASE,
)

_SUBTOTAL_RE = re.compile(r"\bsub\s*total\b", re.IGNORECASE)
_TAX_RE = re.compile(r"\b(?:sales\s+)?tax\b", re.IGNORECASE)
_TOTAL_RE = re.compile(
    r"\b(?:grand\s+total|total|amount\s+due|balance\s+due)\b", re.IGNORECASE
)
_TOTAL_SUMMARY_RE = re.compile(
    r"\btotal\s+(?:discounts?|items?|savings?)\b", re.IGNORECASE
)

_METADATA_RE = re.compile(
    r"\b(?:amex|balance|card|cash|cashier|change|credit|customer|date|debit|"
    r"demo\s+tender|discover|fixture|mastercard|member|not\s+a\s+real|order|"
    r"gratuity|payment|receipt|sample|synthetic|tender|thank|time|tip|transaction|visa|"
    r"welcome)\b",
    re.IGNORECASE,
)

_OCR_LOCK = threading.Lock()


def _ascii_fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("\u00b7", " ").replace("|", " ")
    value = re.sub(r"(?<=[A-Za-z])\$(?=\s|$)", "S", value)
    value = re.sub(r"(?<=\d)0Z\b", "OZ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _coerce_box(raw_box: Any) -> Box:
    try:
        points = tuple((float(point[0]), float(point[1])) for point in raw_box)
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("RapidOCR returned an invalid text box.") from exc
    if not points:
        raise ValueError("RapidOCR returned an empty text box.")
    return points


def tokens_from_rapidocr_output(output: Any) -> list[OcrToken]:
    """Normalize both current RapidOCROutput and legacy tuple results."""

    if output is None:
        return []

    boxes = getattr(output, "boxes", None)
    texts = getattr(output, "txts", None)
    scores = getattr(output, "scores", None)
    if boxes is not None and texts is not None and scores is not None:
        records = zip(boxes, texts, scores, strict=False)
    elif isinstance(output, dict) and {
        "boxes",
        "txts",
        "scores",
    }.issubset(output):
        records = zip(
            output["boxes"], output["txts"], output["scores"], strict=False
        )
    else:
        legacy = output
        if (
            isinstance(output, tuple)
            and len(output) == 2
            and isinstance(output[0], (list, tuple))
        ):
            legacy = output[0]
        records = (
            (record[0], record[1], record[2])
            for record in (legacy or [])
            if isinstance(record, (list, tuple)) and len(record) >= 3
        )

    tokens: list[OcrToken] = []
    for raw_box, raw_text, raw_score in records:
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            score = max(0.0, min(1.0, float(raw_score)))
            box = _coerce_box(raw_box)
        except (TypeError, ValueError):
            continue
        tokens.append(OcrToken(text=text, box=box, confidence=score))
    return tokens


def _vertical_overlap(left: OcrToken, right: OcrToken) -> float:
    overlap = max(0.0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return overlap / max(1.0, min(left.height, right.height))


def _same_visual_line(token: OcrToken, group: Sequence[OcrToken]) -> bool:
    representative = min(group, key=lambda other: abs(other.center_y - token.center_y))
    center_distance = abs(representative.center_y - token.center_y)
    return _vertical_overlap(token, representative) >= 0.35 or center_distance <= max(
        token.height, representative.height
    ) * 0.55


def _line_from_tokens(tokens: Sequence[OcrToken]) -> OcrLine:
    ordered = tuple(sorted(tokens, key=lambda token: token.left))
    left = min(token.left for token in ordered)
    right = max(token.right for token in ordered)
    top = min(token.top for token in ordered)
    bottom = max(token.bottom for token in ordered)
    # The lowest token confidence is retained because a low-confidence price is
    # more important than a high-confidence product description.
    confidence = min(token.confidence for token in ordered)
    return OcrLine(
        text=" ".join(token.text.strip() for token in ordered if token.text.strip()),
        box=((left, top), (right, top), (right, bottom), (left, bottom)),
        confidence=confidence,
        tokens=ordered,
    )


def reconstruct_lines(tokens: Sequence[OcrToken]) -> list[OcrLine]:
    """Join spatially separate OCR boxes that occupy the same receipt row."""

    groups: list[list[OcrToken]] = []
    for token in sorted(tokens, key=lambda item: (item.center_y, item.left)):
        compatible = [group for group in groups if _same_visual_line(token, group)]
        if not compatible:
            groups.append([token])
            continue
        group = min(
            compatible,
            key=lambda candidate: abs(
                sum(item.center_y for item in candidate) / len(candidate)
                - token.center_y
            ),
        )
        group.append(token)

    lines = [_line_from_tokens(group) for group in groups]
    return sorted(lines, key=lambda line: (line.top, line.left))


def _money_matches(value: str) -> list[_MoneyMatch]:
    matches: list[_MoneyMatch] = []
    for match in _MONEY_RE.finditer(value):
        try:
            amount = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        if "-" in prefix or "\u2212" in prefix or "(" in prefix or "-" in suffix:
            amount = -amount
        matches.append(_MoneyMatch(amount, match.start(), match.end()))
    return matches


def _as_money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _valid_quantity(value: float) -> bool:
    return math.isfinite(value) and 0 < value <= 100


def _parse_date(value: str) -> str | None:
    folded = _ascii_fold(value)
    patterns = (
        re.compile(
            r"(?<!\d)(?P<year>20\d{2})[./-](?P<month>0?[1-9]|1[0-2])"
            r"[./-](?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
        ),
        re.compile(
            r"(?<!\d)(?P<month>0?[1-9]|1[0-2])[./-]"
            r"(?P<day>0?[1-9]|[12]\d|3[01])[./-]"
            r"(?P<year>\d{2}|20\d{2})(?!\d)"
        ),
    )
    for pattern in patterns:
        match = pattern.search(folded)
        if match is None:
            continue
        year = int(match.group("year"))
        if year < 100:
            year += 2000 if year < 70 else 1900
        try:
            parsed = date(
                year,
                int(match.group("month")),
                int(match.group("day")),
            )
            return parsed.isoformat()
        except ValueError:
            continue

    named = re.search(
        r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?[,]?\s+(?P<year>20\d{2})\b",
        folded,
        re.IGNORECASE,
    )
    if named is None:
        return None
    month_names = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    try:
        return date(
            int(named.group("year")),
            month_names[named.group("month")[:3].lower()],
            int(named.group("day")),
        ).isoformat()
    except ValueError:
        return None


def _anchor_amount(value: str) -> float | None:
    amounts = _money_matches(value)
    return _as_money(amounts[-1].value) if amounts else None


def _is_subtotal(value: str) -> bool:
    return _SUBTOTAL_RE.search(_ascii_fold(value)) is not None


def _is_tax(value: str) -> bool:
    return _TAX_RE.search(_ascii_fold(value)) is not None


def _is_total(value: str) -> bool:
    folded = _ascii_fold(value)
    return (
        not _is_subtotal(folded)
        and _TOTAL_SUMMARY_RE.search(folded) is None
        and _TOTAL_RE.search(folded) is not None
    )


def _clean_name(value: str) -> str:
    value = _ascii_fold(value)
    value = re.sub(r"^[#*.:\-]+|[#*.:\-]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _unit_from_at(value: str | None) -> str:
    normalized = (value or "").lower()
    return {
        "lb": "lb",
        "lbs": "lb",
        "oz": "oz",
        "kg": "kg",
        "g": "g",
    }.get(normalized, "each")


def _parse_package_suffix(value: str) -> tuple[str, float, str] | None:
    match = _PACKAGE_RE.search(value)
    if match is None:
        return None
    name = _clean_name(value[: match.start()])
    if not name:
        return None
    quantity_text = match.group("qty")
    quantity = float(quantity_text)
    raw_unit = re.sub(r"\s+", "", match.group("unit").lower())

    if raw_unit in {"lb", "lbs", "kg"} and "." in quantity_text:
        return name, quantity, "lb" if raw_unit.startswith("lb") else "kg"
    if raw_unit in {"gal", "gallon", "gallons"}:
        return name, quantity, "gallon"
    if raw_unit in {"l", "ltr", "liter", "liters"}:
        return name, quantity, "liter"
    if raw_unit in {"doz", "dozen", "dozens"}:
        return name, quantity, "dozen"
    if raw_unit in {"pk", "pack", "packs", "ct", "count", "counts"}:
        return name, 1.0, "pack"
    if raw_unit in {"lb", "lbs", "kg", "floz", "oz", "g"}:
        return name, 1.0, "pack" if raw_unit in {"lb", "lbs", "kg"} else "each"
    return name, 1.0, "each"


def _discount_item(
    raw_text: str, name: str, quantity: float, line_total: Decimal, confidence: float
) -> ReceiptLineItem:
    total = -abs(line_total)
    unit_price = total / Decimal(str(max(quantity, 0.01)))
    return ReceiptLineItem(
        raw_text=raw_text,
        name=_clean_name(name).title() or "Discount",
        canonical_key=None,
        qty=quantity,
        unit="each",
        unit_price=_as_money(unit_price),
        line_total=_as_money(total),
        category="non_food",
        is_perishable=False,
        storage=None,
        eat_by_window=None,
        confidence=max(0.0, min(1.0, confidence)),
        needs_review=confidence < 0.75,
    )


def _parse_item_line(line: OcrLine) -> ReceiptLineItem | None:
    raw_text = line.text.strip()
    value = _ascii_fold(raw_text)
    if (
        not value
        or _is_subtotal(value)
        or _is_tax(value)
        or _is_total(value)
        or _TOTAL_SUMMARY_RE.search(value)
    ):
        return None
    if _METADATA_RE.search(value):
        return None

    amounts = _money_matches(value)
    at_match = _AT_RE.search(value)
    invalid_quantity = False
    if at_match is not None:
        parsed_quantity = float(at_match.group("qty"))
        invalid_quantity = not _valid_quantity(parsed_quantity)
        following = [amount for amount in amounts if amount.start >= at_match.end()]
        if following:
            total_match = following[-1]
            line_total = total_match.value
            core = (value[: total_match.start] + " " + value[total_match.end :]).strip()
        else:
            quantity = Decimal("1") if invalid_quantity else Decimal(at_match.group("qty"))
            unit_price = Decimal(at_match.group("price").replace(",", ""))
            line_total = quantity * unit_price
            core = value

        core_at = _AT_RE.search(core)
        if core_at is None:
            return None
        quantity = float(core_at.group("qty"))
        if not _valid_quantity(quantity):
            quantity = 1.0
        unit = _unit_from_at(core_at.group("unit") or core_at.group("price_unit"))
        unit_price = Decimal(core_at.group("price").replace(",", ""))
        name = _clean_name(core[: core_at.start()] + " " + core[core_at.end() :])
    else:
        if not amounts:
            return None
        total_match = amounts[-1]
        line_total = total_match.value
        core = _clean_name(value[: total_match.start] + " " + value[total_match.end :])
        quantity = 1.0
        unit = "each"
        unit_price = line_total

        suffix_quantity = _QUANTITY_SUFFIX_RE.search(core)
        if suffix_quantity is not None:
            parsed_quantity = float(suffix_quantity.group("qty"))
            core = _clean_name(core[: suffix_quantity.start()])
            if _valid_quantity(parsed_quantity):
                quantity = parsed_quantity
                unit_price = line_total / Decimal(str(quantity))
            else:
                invalid_quantity = True
        else:
            prefix_quantity = _QUANTITY_PREFIX_RE.match(core)
            if prefix_quantity is not None:
                parsed_quantity = float(prefix_quantity.group("qty"))
                core = _clean_name(prefix_quantity.group("name"))
                if _valid_quantity(parsed_quantity):
                    quantity = parsed_quantity
                    unit_price = line_total / Decimal(str(quantity))
                else:
                    invalid_quantity = True
            else:
                package = _parse_package_suffix(core)
                if package is not None:
                    core, quantity, unit = package
                    if _valid_quantity(quantity):
                        unit_price = line_total / Decimal(str(quantity))
                    else:
                        invalid_quantity = True
                        quantity = 1.0

        name = core

    if not name or len(re.sub(r"[^A-Za-z]", "", name)) < 2:
        return None
    if not _valid_quantity(quantity):
        invalid_quantity = True
        quantity = 1.0

    if _DISCOUNT_RE.search(name) or line_total < 0:
        discount_confidence = min(line.confidence, 0.5) if invalid_quantity else line.confidence
        return _discount_item(
            raw_text, name, quantity, line_total, discount_confidence
        )

    product = match_product(name, ocr_confidence=line.confidence)
    if unit == "each" and product.category == "frozen":
        unit = "pack"
    confidence = max(0.0, min(1.0, min(line.confidence, product.confidence)))
    return ReceiptLineItem(
        raw_text=raw_text,
        name=product.name,
        canonical_key=product.canonical_key,
        qty=round(quantity, 3),
        unit=unit,  # type: ignore[arg-type]
        unit_price=_as_money(unit_price),
        line_total=_as_money(line_total),
        category=product.category,  # type: ignore[arg-type]
        is_perishable=product.is_perishable,
        storage=None,
        eat_by_window=None,
        confidence=confidence,
        needs_review=product.needs_review or confidence < 0.75 or invalid_quantity,
    )


def _store_name(lines: Sequence[OcrLine]) -> str | None:
    for line in lines[:8]:
        value = _ascii_fold(line.text)
        if (
            len(re.sub(r"[^A-Za-z]", "", value)) < 4
            or any(char.isdigit() for char in value)
            or _METADATA_RE.search(value)
            or _is_subtotal(value)
            or _is_tax(value)
            or _is_total(value)
        ):
            continue
        cleaned = re.sub(r"[^A-Za-z0-9&' -]+", " ", value)
        return re.sub(r"\s+", " ", cleaned).strip().title()
    return None


def parse_receipt_lines(lines: Sequence[OcrLine]) -> ReceiptParse:
    """Pure deterministic parser for reconstructed English receipt lines."""

    purchased_at = next(
        (parsed for line in lines if (parsed := _parse_date(line.text)) is not None),
        None,
    )
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    items: list[ReceiptLineItem] = []

    for line in lines:
        if _is_subtotal(line.text):
            subtotal = _anchor_amount(line.text)
            continue
        if _is_tax(line.text):
            tax = _anchor_amount(line.text)
            continue
        if _is_total(line.text):
            total = _anchor_amount(line.text)
            continue
        item = _parse_item_line(line)
        if item is not None:
            items.append(item)

    raw_confidence = (
        sum(line.confidence for line in lines) / len(lines) if lines else 0.0
    )
    if items:
        overall_confidence = sum(item.confidence for item in items) / len(items)
    else:
        overall_confidence = raw_confidence * 0.5

    store_name = _store_name(lines)
    has_financial_anchor = subtotal is not None or total is not None
    has_cropped_structure = len(items) >= 2 and bool(store_name or purchased_at)
    if not lines:
        quality_issue = "not_a_receipt"
    elif not has_financial_anchor and not has_cropped_structure:
        quality_issue = "cropped" if store_name or purchased_at else "not_a_receipt"
    elif raw_confidence < 0.45:
        quality_issue = "blurry"
    elif not items:
        quality_issue = "cropped"
    elif not has_financial_anchor:
        quality_issue = "cropped"
    else:
        quality_issue = None

    if quality_issue == "cropped" and not has_financial_anchor:
        items = [item.model_copy(update={"needs_review": True}) for item in items]
    if quality_issue == "not_a_receipt":
        overall_confidence = min(overall_confidence, 0.49)

    return ReceiptParse(
        store_name=store_name,
        purchased_at=purchased_at,
        subtotal=subtotal,
        tax=tax,
        total=total,
        overall_confidence=round(max(0.0, min(1.0, overall_confidence)), 3),
        image_quality_issue=quality_issue,
        items=items,
    )


def parse_ocr_tokens(tokens: Sequence[OcrToken]) -> ReceiptParse:
    return parse_receipt_lines(reconstruct_lines(tokens))


def parse_receipt_text(text: str) -> ReceiptParse:
    """Build geometry-free lines for parser tests and diagnostic CLI use."""

    lines: list[OcrLine] = []
    for index, raw_line in enumerate(text.splitlines()):
        if not raw_line.strip():
            continue
        top = float(index * 20)
        token = OcrToken(
            text=raw_line.strip(),
            box=((0.0, top), (500.0, top), (500.0, top + 15), (0.0, top + 15)),
            confidence=1.0,
        )
        lines.append(_line_from_tokens([token]))
    return parse_receipt_lines(lines)


def _create_ocr_engine() -> Any:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise AppError(
            503,
            "LOCAL_OCR_UNAVAILABLE",
            "RapidOCR is not installed in the server environment.",
            "Local receipt scanning is temporarily unavailable.",
        ) from exc
    return RapidOCR()


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    return _create_ocr_engine()


def _run_rapidocr(jpeg_bytes: bytes) -> list[OcrToken]:
    if not jpeg_bytes:
        raise AppError(
            422,
            "INVALID_IMAGE",
            "The local OCR input was empty.",
            "Choose a receipt photo and try again.",
        )
    try:
        with _OCR_LOCK:
            output = _ocr_engine()(jpeg_bytes)
        return tokens_from_rapidocr_output(output)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            422,
            "LOCAL_OCR_FAILED",
            f"RapidOCR could not read the receipt image: {exc}",
            "Retake the receipt photo in even light and try again.",
        ) from exc


async def parse_local_receipt_image(jpeg_bytes: bytes) -> ReceiptParse:
    """Run one local CPU OCR pass, then parse it without any cloud call."""

    tokens = await asyncio.to_thread(_run_rapidocr, jpeg_bytes)
    return parse_ocr_tokens(tokens)
