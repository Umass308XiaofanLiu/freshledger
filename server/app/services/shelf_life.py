from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..errors import AppError
from ..models import (
    EatByWindow,
    ReceiptDraftItem,
    ReceiptLineItem,
    StorageOptions,
    StoragePlan,
)


DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "shelf_life.csv"
GLOBAL_MIN_DAYS = 1
GLOBAL_MAX_DAYS = 730

CATEGORY_MAX_DAYS: dict[str, int] = {
    "seafood": 3,
    "meat": 5,
    "dairy": 14,
    "deli": 5,
    "produce": 14,
    "bakery": 7,
    "frozen": 270,
    "beverage": 21,
    "pantry_staple": 365,
    "unknown": 3,
}


@dataclass(frozen=True)
class CategoryDefault:
    method: str
    temp_c: float
    duration_days: int
    fridge_days: int | None
    freezer_days: int | None
    pantry_days: int | None


CATEGORY_DEFAULTS: dict[str, CategoryDefault] = {
    "seafood": CategoryDefault("fridge", 1, 2, 2, 90, None),
    "meat": CategoryDefault("fridge", 2, 2, 2, 120, None),
    "dairy": CategoryDefault("fridge", 4, 7, 7, 90, None),
    "deli": CategoryDefault("fridge", 4, 4, 4, 30, None),
    "produce": CategoryDefault("fridge", 4, 5, 5, 180, 5),
    "bakery": CategoryDefault("pantry", 20, 4, 7, 90, 4),
    "frozen": CategoryDefault("freezer", -18, 90, None, 90, None),
    "beverage": CategoryDefault("fridge", 4, 7, 7, None, 180),
    "pantry_staple": CategoryDefault("pantry", 20, 365, None, 365, 365),
    "unknown": CategoryDefault("fridge", 4, 3, 3, 30, None),
}

PREPARED_COMPOUND_TOKENS = {
    "bowl",
    "cooked",
    "leftover",
    "leftovers",
    "meal",
    "prepared",
    "pudding",
    "salad",
    "sandwich",
    "soup",
    "wrap",
}

PANTRY_CONTRADICTION_TOKENS = PREPARED_COMPOUND_TOKENS | {
    "chilled",
    "fresh",
    "frozen",
    "moist",
    "opened",
    "refrigerated",
    "wet",
}

REFERENCE_IGNORED_MODIFIERS = {
    "boneless",
    "brand",
    "large",
    "medium",
    "organic",
    "small",
}


@dataclass(frozen=True)
class ShelfLifeRow:
    canonical_key: str
    display_name: str
    category: str
    recommended_method: str
    fridge_days: int | None
    freezer_days: int | None
    pantry_days: int | None
    temp_c: float
    eat_by_start_days: int
    best_by_days: int | None
    aliases: tuple[str, ...]
    notes: str

    def days_for(self, method: str) -> int | None:
        return {
            "fridge": self.fridge_days,
            "freezer": self.freezer_days,
            "pantry": self.pantry_days,
        }.get(method)


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _condition_is_compatible(item: ReceiptLineItem, row: ShelfLifeRow) -> bool:
    item_tokens = set(_normalize(item.name).split())
    row_text = " ".join(
        (
            row.canonical_key.replace("_", " "),
            row.display_name,
            *row.aliases,
            row.notes,
        )
    )
    row_tokens = set(_normalize(row_text).split())
    if item_tokens & PANTRY_CONTRADICTION_TOKENS and row.category == "pantry_staple":
        return False
    condition_groups = (
        PREPARED_COMPOUND_TOKENS,
        {"raw", "uncooked"},
        {"opened"},
        {"unopened"},
        {"frozen"},
        {"dry", "dried"},
    )
    return all(
        not (item_tokens & group) or bool(row_tokens & group)
        for group in condition_groups
    )


@lru_cache(maxsize=1)
def load_reference_rows() -> tuple[ShelfLifeRow, ...]:
    if not DATA_PATH.exists():
        return ()

    rows: list[ShelfLifeRow] = []
    with DATA_PATH.open("r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            aliases = tuple(
                alias.strip().lower()
                for alias in json.loads(record.get("aliases") or "[]")
                if alias.strip()
            )
            row = ShelfLifeRow(
                canonical_key=record["canonical_key"].strip(),
                display_name=record["display_name"].strip(),
                category=record["category"].strip(),
                recommended_method=record["recommended_method"].strip(),
                fridge_days=_optional_int(record.get("fridge_days")),
                freezer_days=_optional_int(record.get("freezer_days")),
                pantry_days=_optional_int(record.get("pantry_days")),
                temp_c=float(record["temp_c"]),
                eat_by_start_days=int(record.get("eat_by_start_days") or 0),
                best_by_days=_optional_int(record.get("best_by_days")),
                aliases=aliases,
                notes=(record.get("notes") or "").strip(),
            )
            if row.days_for(row.recommended_method) is None:
                raise RuntimeError(
                    f"Shelf-life row {row.canonical_key!r} has no duration for its "
                    f"recommended method {row.recommended_method!r}."
                )
            rows.append(row)
    return tuple(rows)


def _identity_forms(value: str, row: ShelfLifeRow) -> set[str]:
    normalized = _normalize(value)
    if not normalized:
        return set()
    ignored = set(REFERENCE_IGNORED_MODIFIERS)
    # "Fresh" is a harmless merchandising modifier for produce/meat/bakery,
    # but safety-material for dry pantry staples such as pasta and rice.
    if row.category != "pantry_staple":
        ignored.add("fresh")
    compact = " ".join(
        token for token in normalized.split() if token not in ignored
    )
    return {form for form in (normalized, compact) if form}


def _identity_matches_reference(item: ReceiptLineItem, row: ShelfLifeRow) -> bool:
    aliases = {
        _normalize(alias)
        for alias in (
            *row.aliases,
            row.display_name,
            row.canonical_key.replace("_", " "),
        )
        if _normalize(alias)
    }
    return bool(_identity_forms(item.name, row) & aliases)


def find_reference(item: ReceiptLineItem) -> ShelfLifeRow | None:
    rows = tuple(
        row for row in load_reference_rows() if row.category == item.category
    )
    if item.canonical_key:
        key = item.canonical_key.strip().lower()
        exact = next(
            (
                row
                for row in rows
                if row.canonical_key == key
                and _identity_matches_reference(item, row)
                and _condition_is_compatible(item, row)
            ),
            None,
        )
        if exact is not None:
            return exact

    matches: list[ShelfLifeRow] = []
    for row in rows:
        if not _condition_is_compatible(item, row) or not _identity_matches_reference(
            item, row
        ):
            continue
        matches.append(row)
    canonical_keys = {row.canonical_key for row in matches}
    return matches[0] if len(canonical_keys) == 1 else None


def _temperature_for(method: str, reference_temp: float | None = None) -> float:
    if method == "freezer":
        return -18
    if method == "pantry":
        return 20
    if reference_temp is not None and 0 <= reference_temp <= 4:
        return reference_temp
    return 4


def _single_method_options(method: str, duration_days: int) -> StorageOptions:
    payload: dict[str, int | None] = {
        "fridge_days": None,
        "freezer_days": None,
        "pantry_days": None,
    }
    payload[f"{method}_days"] = duration_days
    return StorageOptions.model_validate(payload)


def _default_for(item: ReceiptLineItem) -> CategoryDefault:
    # A perishable item that only resembles a dry pantry staple is contradictory.
    # Treat it as unknown refrigerated food until a human or reference row can
    # identify it; never inherit the 365-day dry-goods default.
    if item.is_perishable and item.category == "pantry_staple":
        return CATEGORY_DEFAULTS["unknown"]
    return CATEGORY_DEFAULTS.get(item.category, CATEGORY_DEFAULTS["unknown"])


def _default_result(
    item: ReceiptLineItem,
    *,
    item_id: int,
    default: CategoryDefault,
    category_max: int,
    force_review: bool,
) -> ReceiptDraftItem:
    duration = max(
        GLOBAL_MIN_DAYS,
        min(default.duration_days, category_max, GLOBAL_MAX_DAYS),
    )
    safe_item = (
        item.model_copy(update={"needs_review": True}) if force_review else item
    )
    return ReceiptDraftItem(
        **safe_item.model_dump(exclude={"storage", "eat_by_window"}),
        item_id=item_id,
        excluded=False,
        storage=StoragePlan(
            method=default.method,  # type: ignore[arg-type]
            temp_c=_temperature_for(default.method, default.temp_c),
            duration_days=duration,
        ),
        storage_options=_single_method_options(default.method, duration),
        eat_by_window=EatByWindow(start_days=0, end_days=duration),
        shelf_life_source="default",
    )


def _sanitize_item(item: ReceiptLineItem) -> ReceiptLineItem:
    qty = max(0.01, min(100.0, item.qty))
    unit_price = max(-500.0, min(500.0, item.unit_price))
    line_total = max(-500.0, min(500.0, item.line_total))
    changed = (
        qty != item.qty
        or unit_price != item.unit_price
        or line_total != item.line_total
    )
    if not changed:
        return item
    return item.model_copy(
        update={
            "qty": qty,
            "unit_price": unit_price,
            "line_total": line_total,
            "needs_review": True,
        }
    )


def resolve_item(
    item: ReceiptLineItem,
    *,
    item_id: int,
    excluded: bool | None = None,
    method_override: str | None = None,
    allow_reference: bool = True,
) -> ReceiptDraftItem:
    item = _sanitize_item(item)
    excluded = item.category == "non_food" if excluded is None else excluded
    if excluded or item.category == "non_food":
        return ReceiptDraftItem(
            **item.model_dump(
                exclude={"storage", "eat_by_window", "is_perishable", "canonical_key"}
            ),
            item_id=item_id,
            excluded=True,
            is_perishable=False,
            canonical_key=None,
            storage=None,
            storage_options=None,
            eat_by_window=None,
            shelf_life_source=None,
        )

    # A model-classified perishable pantry staple is internally contradictory:
    # short aliases such as "rice" or "pasta" must not turn rice pudding or
    # pasta salad into a 365-day dry-good recommendation.
    contradictory_pantry_identity = item.category == "pantry_staple" and (
        item.is_perishable
        or bool(set(_normalize(item.name).split()) & PANTRY_CONTRADICTION_TOKENS)
    )
    if contradictory_pantry_identity:
        item = item.model_copy(
            update={
                "canonical_key": None,
                "category": "unknown",
                "is_perishable": True,
                "storage": None,
                "eat_by_window": None,
                "needs_review": True,
            }
        )
    reference = (
        None
        if contradictory_pantry_identity or not allow_reference
        else find_reference(item)
    )
    unresolved_prepared_identity = (
        reference is None
        and bool(set(_normalize(item.name).split()) & PREPARED_COMPOUND_TOKENS)
    )
    if unresolved_prepared_identity and not contradictory_pantry_identity:
        item = item.model_copy(
            update={
                "canonical_key": None,
                "category": "unknown",
                "is_perishable": True,
                "storage": None,
                "eat_by_window": None,
                "needs_review": True,
            }
        )
    elif reference is None and item.canonical_key is not None:
        # A model-supplied or edited canonical key is untrusted unless the clean
        # item name is also an exact explicit alias for that reference row.
        item = item.model_copy(
            update={"canonical_key": None, "needs_review": True}
        )
    if reference is not None:
        method = method_override or reference.recommended_method
        duration = reference.days_for(method)
        if duration is None:
            raise AppError(
                422,
                "STORAGE_NOT_RECOMMENDED",
                f"{method} storage is not recommended for {reference.display_name}.",
                f"Choose another storage location for {item.name}.",
            )
        duration = max(GLOBAL_MIN_DAYS, min(GLOBAL_MAX_DAYS, duration))
        end_days = duration
        if method == reference.recommended_method and reference.best_by_days is not None:
            end_days = max(0, min(duration, reference.best_by_days))
        start_days = max(0, min(end_days, reference.eat_by_start_days))
        return ReceiptDraftItem(
            **item.model_dump(exclude={"canonical_key", "storage", "eat_by_window"}),
            item_id=item_id,
            excluded=False,
            canonical_key=reference.canonical_key,
            storage=StoragePlan(
                method=method,  # type: ignore[arg-type]
                temp_c=_temperature_for(method, reference.temp_c),
                duration_days=duration,
            ),
            storage_options=StorageOptions(
                fridge_days=reference.fridge_days,
                freezer_days=reference.freezer_days,
                pantry_days=reference.pantry_days,
            ),
            eat_by_window=EatByWindow(start_days=start_days, end_days=end_days),
            shelf_life_source="reference",
        )

    default = _default_for(item)
    category_max = CATEGORY_MAX_DAYS.get(item.category, CATEGORY_MAX_DAYS["unknown"])

    if item.storage is not None:
        allowed_for_method = {
            "fridge": default.fridge_days,
            "freezer": default.freezer_days,
            "pantry": default.pantry_days,
        }.get(item.storage.method)
        unsafe_method = allowed_for_method is None or (
            item.is_perishable and item.storage.method != default.method
        )
        if unsafe_method:
            if method_override is not None and method_override != default.method:
                raise AppError(
                    422,
                    "STORAGE_NOT_RECOMMENDED",
                    "The requested storage location is unsafe for this food category.",
                    f"Keep the conservative {default.method} recommendation for {item.name}.",
                )
            return _default_result(
                item,
                item_id=item_id,
                default=default,
                category_max=category_max,
                force_review=True,
            )
        if method_override is not None and method_override != item.storage.method:
            raise AppError(
                422,
                "STORAGE_NOT_RECOMMENDED",
                "Cross-location overrides require an exact shelf-life reference match.",
                f"Keep the suggested storage location for {item.name}.",
            )
        method = item.storage.method
        duration = max(
            GLOBAL_MIN_DAYS,
            min(GLOBAL_MAX_DAYS, category_max, item.storage.duration_days),
        )
        options = _single_method_options(method, duration)
        if item.eat_by_window is None:
            start_days, end_days = 0, duration
        else:
            end_days = max(0, min(duration, item.eat_by_window.end_days))
            start_days = max(0, min(end_days, item.eat_by_window.start_days))
        return ReceiptDraftItem(
            **item.model_dump(exclude={"storage", "eat_by_window"}),
            item_id=item_id,
            excluded=False,
            storage=StoragePlan(
                method=method,  # type: ignore[arg-type]
                temp_c=_temperature_for(method, item.storage.temp_c),
                duration_days=duration,
            ),
            storage_options=options,
            eat_by_window=EatByWindow(start_days=start_days, end_days=end_days),
            shelf_life_source="llm_clamped",
        )

    if item.is_perishable:
        if method_override is not None and method_override != default.method:
            raise AppError(
                422,
                "STORAGE_NOT_RECOMMENDED",
                "Cross-location overrides require an exact shelf-life reference match.",
                f"Keep the suggested storage location for {item.name}.",
            )
        return _default_result(
            item,
            item_id=item_id,
            default=default,
            category_max=category_max,
            force_review=item.category == "pantry_staple",
        )

    return ReceiptDraftItem(
        **item.model_dump(exclude={"storage", "eat_by_window"}),
        item_id=item_id,
        excluded=False,
        storage=None,
        storage_options=None,
        eat_by_window=None,
        shelf_life_source=None,
    )


def clear_reference_cache() -> None:
    load_reference_rows.cache_clear()
