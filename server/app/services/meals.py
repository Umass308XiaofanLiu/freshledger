from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pydantic import ValidationError

from ..db import connect, init_database
from ..models import (
    DemoGeneration,
    MealsTodayResponse,
    MealSuggestion,
    MealUse,
)
from .receipt_store import PantryItemRecord, list_pantry_items


_MEAL_TEMPLATE_VERSION = "credible-templates-v5"


@dataclass(frozen=True)
class _MealCandidate:
    name: str
    items: tuple[PantryItemRecord, ...]
    steps: tuple[str, ...]
    time_minutes: int
    priority: int


def _days_left(item: PantryItemRecord, today: date) -> int:
    return (date.fromisoformat(item.best_by) - today).days


def _safe_days_left(item: PantryItemRecord, today: date) -> int:
    return (date.fromisoformat(item.safe_until) - today).days


def _pantry_hash(items: list[PantryItemRecord]) -> str:
    parts = [
        "|".join(
            (
                str(item.id),
                item.name,
                item.canonical_key or "",
                item.category,
                format(item.qty_remaining, ".8g"),
                item.unit,
                str(item.unit_price_cents),
                item.best_by,
                item.safe_until,
            )
        )
        for item in items
    ]
    cache_material = "\n".join([_MEAL_TEMPLATE_VERSION, *parts])
    return hashlib.sha256(cache_material.encode("utf-8")).hexdigest()


def _tracked_value_cents(items: list[PantryItemRecord]) -> int:
    value = sum(
        Decimal(str(item.qty_remaining)) * Decimal(item.unit_price_cents)
        for item in items
    )
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _search_text(item: PantryItemRecord) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        f"{item.canonical_key or ''} {item.name}".lower(),
    ).strip()


def _has_any(item: PantryItemRecord, *tokens: str) -> bool:
    text = _search_text(item)
    return any(token in text for token in tokens)


def _first(
    items: list[PantryItemRecord],
    predicate,
    *,
    excluding: tuple[PantryItemRecord, ...] = (),
) -> PantryItemRecord | None:
    excluded_ids = {item.id for item in excluding}
    return next(
        (
            item
            for item in items
            if item.id not in excluded_ids and predicate(item)
        ),
        None,
    )


def _dedupe(items: list[PantryItemRecord]) -> tuple[PantryItemRecord, ...]:
    seen: set[int] = set()
    result: list[PantryItemRecord] = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return tuple(result)


def _candidate_has_urgent(
    candidate: _MealCandidate, urgent_ids: set[int]
) -> bool:
    return any(item.id in urgent_ids for item in candidate.items)


def _attach_urgent_beverage(
    candidate: _MealCandidate,
    urgent: list[PantryItemRecord],
) -> _MealCandidate | None:
    if not urgent or _candidate_has_urgent(candidate, {item.id for item in urgent}):
        return candidate
    beverage = _first(urgent, lambda item: item.category == "beverage")
    if beverage is None:
        return None
    side_step = f"Serve {beverage.name} chilled on the side; do not cook or heat it."
    steps = candidate.steps + (side_step,)
    if len(steps) > 3:
        steps = candidate.steps[:2] + (side_step,)
    return replace(
        candidate,
        items=_dedupe([*candidate.items, beverage]),
        steps=steps,
    )


def _template_candidates(
    items: list[PantryItemRecord], today: date
) -> list[_MealCandidate]:
    urgent = [item for item in items if _days_left(item, today) <= 3]
    candidates: list[_MealCandidate] = []

    juice = _first(
        items,
        lambda item: item.category == "beverage" and _has_any(item, "juice"),
    )
    berries = _first(items, lambda item: _has_any(item, "berr"))
    yogurt = _first(items, lambda item: _has_any(item, "yogurt"))
    if juice and berries and yogurt:
        smoothie_name = (
            "Orange-Berry Yogurt Smoothie"
            if _has_any(juice, "orange")
            else f"{juice.name} Berry-Yogurt Smoothie"
        )
        candidates.append(
            _MealCandidate(
                name=smoothie_name,
                items=(juice, berries, yogurt),
                steps=(
                    f"Thaw {berries.name} only as directed on its package.",
                    f"Blend {juice.name}, {berries.name}, and {yogurt.name} until smooth.",
                    "Serve immediately and refrigerate any unused ingredients promptly.",
                ),
                time_minutes=10,
                priority=10,
            )
        )

    eggs = _first(items, lambda item: _has_any(item, "egg"))
    beans = _first(items, lambda item: _has_any(item, "bean"))
    rice = _first(items, lambda item: _has_any(item, "rice"))
    if eggs and beans and rice:
        candidates.append(
            _MealCandidate(
                name="Egg, Black Bean & Rice Bowl",
                items=(eggs, beans, rice),
                steps=(
                    f"Cook {eggs.name} fully, following package and USDA egg-safety guidance.",
                    f"Heat {beans.name} and prepare {rice.name} by their package directions, then assemble the bowl.",
                    "Season with salt, pepper, cooking oil, or dried spices and serve promptly.",
                ),
                time_minutes=30,
                priority=20,
            )
        )

    if berries and yogurt:
        candidates.append(
            _MealCandidate(
                name="Berry-Yogurt Breakfast",
                items=(berries, yogurt),
                steps=(
                    f"Thaw {berries.name} only as directed on its package.",
                    f"Spoon {yogurt.name} with {berries.name} and serve promptly.",
                ),
                time_minutes=5,
                priority=30,
            )
        )

    proteins = [item for item in items if item.category in {"meat", "seafood"}]
    greens = [
        item
        for item in items
        if item.category == "produce"
        and _has_any(item, "green", "spinach", "lettuce", "kale")
    ]
    avocados = [item for item in items if _has_any(item, "avocado")]
    other_vegetables = [
        item
        for item in items
        if item.category == "produce"
        and not _has_any(item, "banana", "apple", "berr")
        and item not in greens
        and item not in avocados
    ]
    for index, protein in enumerate(proteins[:2]):
        preferred = (
            [*greens, *other_vegetables, *avocados]
            if protein.category == "seafood"
            else [*avocados, *greens, *other_vegetables]
        )
        vegetable = preferred[0] if preferred else None
        used = _dedupe([protein, *([vegetable] if vegetable else [])])
        names = " and ".join(item.name for item in used)
        style = "Skillet" if index == 0 else "Tray"
        candidates.append(
            _MealCandidate(
                name=f"{names} Safe-Cook {style}",
                items=used,
                steps=(
                    f"Check {names} against package guidance and tracked dates before cooking.",
                    f"Cook {protein.name} to the USDA safe internal temperature and prepare any produce separately.",
                    "Season with salt, pepper, cooking oil, or dried spices and serve promptly.",
                ),
                time_minutes=40,
                priority=40 + index,
            )
        )

    bread = _first(items, lambda item: item.category == "bakery")
    deli_or_cheese = _first(
        items,
        lambda item: item.category == "deli" or _has_any(item, "cheese"),
    )
    if bread and deli_or_cheese:
        candidates.append(
            _MealCandidate(
                name=f"{deli_or_cheese.name} & {bread.name} Snack Plate",
                items=(deli_or_cheese, bread),
                steps=(
                    f"Check {deli_or_cheese.name} and {bread.name} against package guidance and tracked dates.",
                    f"Portion {deli_or_cheese.name} with {bread.name} and serve chilled or at room temperature as directed.",
                ),
                time_minutes=10,
                priority=50,
            )
        )

    deli = _first(items, lambda item: item.category == "deli")
    cheese = _first(items, lambda item: _has_any(item, "cheese"))
    ready_produce = _first(
        items,
        lambda item: item.category == "produce"
        and _has_any(item, "avocado", "green", "lettuce", "spinach"),
    )
    if deli and cheese and ready_produce:
        candidates.append(
            _MealCandidate(
                name=f"{deli.name}, {cheese.name} & {ready_produce.name} Snack Plate",
                items=(deli, cheese, ready_produce),
                steps=(
                    f"Check {deli.name} and {cheese.name} against package guidance and tracked dates.",
                    f"Wash {ready_produce.name} if appropriate, then assemble everything without heating the deli items.",
                    "Serve promptly and return unused chilled ingredients to the refrigerator.",
                ),
                time_minutes=10,
                priority=55,
            )
        )

    finalized: list[_MealCandidate] = []
    for candidate in candidates:
        safe_candidate = _attach_urgent_beverage(candidate, urgent)
        if safe_candidate is not None:
            finalized.append(safe_candidate)
    return finalized


def _fallback_candidates(
    items: list[PantryItemRecord], today: date
) -> list[_MealCandidate]:
    urgent = [item for item in items if _days_left(item, today) <= 3]
    anchors = urgent or items
    candidates: list[_MealCandidate] = []
    for anchor in anchors:
        if anchor.category == "beverage":
            partners = [item for item in items if item.id != anchor.id and item.category != "beverage"]
            for partner in partners[:3]:
                hazardous = partner.category in {"meat", "seafood"}
                preparation = (
                    f"Cook {partner.name} by package directions to the USDA safe internal temperature."
                    if hazardous
                    else f"Prepare {partner.name} according to its package guidance and tracked date."
                )
                candidates.append(
                    _MealCandidate(
                        name=f"{partner.name} with Chilled {anchor.name}",
                        items=(partner, anchor),
                        steps=(
                            preparation,
                            f"Serve {anchor.name} chilled on the side; do not cook or heat it.",
                        ),
                        time_minutes=40 if hazardous else 10,
                        priority=80,
                    )
                )
            continue

        compatible_categories = {
            "meat": {"produce"},
            "seafood": {"produce"},
            "produce": {"dairy", "bakery", "deli", "pantry_staple"},
            "dairy": {"produce", "bakery", "pantry_staple"},
            "bakery": {"dairy", "deli", "produce"},
            "deli": {"bakery", "produce", "dairy"},
            "pantry_staple": {"produce", "dairy", "bakery"},
            "frozen": {"dairy", "produce"},
            "unknown": {"produce", "dairy", "bakery"},
        }.get(anchor.category, {"produce", "dairy", "bakery"})
        partners = [
            item
            for item in items
            if item.id != anchor.id and item.category in compatible_categories
        ][:2]
        for partner in partners or [None]:
            used = _dedupe([anchor, *([partner] if partner else [])])
            hazardous = any(item.category in {"meat", "seafood"} for item in used)
            names = " and ".join(item.name for item in used)
            candidates.append(
                _MealCandidate(
                    name=f"{names} Rescue Plate",
                    items=used,
                    steps=(
                        f"Check {names} against package guidance and tracked dates before preparation.",
                        (
                            "Prepare each item separately by its package directions and cook meat or seafood to USDA safe internal temperatures."
                            if hazardous
                            else "Prepare each item separately by its package directions and serve promptly."
                        ),
                    ),
                    time_minutes=40 if hazardous else 15,
                    priority=90,
                )
            )
    return candidates


def _build_meals(items: list[PantryItemRecord], today: date) -> list[MealSuggestion]:
    if not items:
        return []

    target_count = 3 if len(items) >= 4 else min(2, len(items))
    urgent_ids = {
        item.id for item in items if _days_left(item, today) <= 3
    }
    candidates = [
        *_template_candidates(items, today),
        *_fallback_candidates(items, today),
    ]
    candidates.sort(
        key=lambda candidate: (
            candidate.priority >= 80,
            min(_days_left(item, today) for item in candidate.items),
            candidate.priority,
            candidate.name,
        )
    )
    selected: list[_MealCandidate] = []
    signatures: set[tuple[str, tuple[int, ...]]] = set()
    selected_item_sets: set[frozenset[int]] = set()
    for candidate in candidates:
        if urgent_ids and not _candidate_has_urgent(candidate, urgent_ids):
            continue
        signature = (candidate.name, tuple(item.id for item in candidate.items))
        if signature in signatures:
            continue
        item_set = frozenset(item.id for item in candidate.items)
        if candidate.priority >= 80 and item_set in selected_item_sets:
            continue
        signatures.add(signature)
        selected_item_sets.add(item_set)
        selected.append(candidate)
        if len(selected) == target_count:
            break

    meals: list[MealSuggestion] = []
    for candidate in selected:
        chosen = list(candidate.items)
        anchor = min(
            (item for item in chosen if item.id in urgent_ids),
            default=min(chosen, key=lambda item: (_days_left(item, today), item.id)),
            key=lambda item: (_days_left(item, today), item.id),
        )
        anchor_days = _days_left(anchor, today)
        tracked_value_cents = _tracked_value_cents(chosen)
        value_clause = (
            f"; this prioritizes ${tracked_value_cents / 100:.2f} of tracked pantry value"
            if tracked_value_cents > 0
            else ""
        )
        if urgent_ids:
            timing = (
                "today" if anchor_days == 0 else f"within {anchor_days} day"
                if anchor_days == 1
                else f"within {anchor_days} days"
            )
            why_now = (
                f"{anchor.name} is best used {timing}{value_clause}."
            )
        else:
            why_now = (
                f"{anchor.name} is the soonest item in the active pantry"
                f"{value_clause}."
            )

        meals.append(
            MealSuggestion(
                name=candidate.name,
                uses=[
                    MealUse(
                        pantry_item_id=item.id,
                        name=item.name,
                        days_left=_days_left(item, today),
                    )
                    for item in chosen
                ],
                why_now=why_now,
                steps=list(candidate.steps),
                time_minutes=candidate.time_minutes,
            )
        )
    return meals


def get_meals_today(
    *,
    refresh: bool = False,
    today: date | None = None,
    database_path: str | Path | None = None,
) -> MealsTodayResponse:
    """Return zero-token suggestions derived only from active, unexpired pantry rows."""

    for_date = today or date.today()
    active = [
        item
        for item in list_pantry_items(status="active", database_path=database_path)
        if item.qty_remaining > 0
        and _days_left(item, for_date) >= 0
        and _safe_days_left(item, for_date) >= 0
    ][:25]
    pantry_hash = _pantry_hash(active)
    init_database(database_path)

    if not refresh:
        with connect(database_path) as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM meal_suggestions
                WHERE for_date = ? AND pantry_hash = ?
                """,
                (for_date.isoformat(), pantry_hash),
            ).fetchone()
        if row is not None:
            try:
                cached = MealsTodayResponse.model_validate_json(row["payload_json"])
                return cached.model_copy(update={"cached": True})
            except ValidationError:
                pass

    response = MealsTodayResponse(
        date=for_date.isoformat(),
        cached=False,
        generation=DemoGeneration(),
        meals=_build_meals(active, for_date),
    )
    with connect(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO meal_suggestions (for_date, pantry_hash, payload_json)
            VALUES (?, ?, ?)
            ON CONFLICT(for_date, pantry_hash) DO UPDATE SET
              payload_json = excluded.payload_json,
              created_at = datetime('now')
            """,
            (for_date.isoformat(), pantry_hash, response.model_dump_json(by_alias=True)),
        )
    return response
