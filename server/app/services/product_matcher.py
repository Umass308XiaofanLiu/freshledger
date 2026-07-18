from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from .shelf_life import ShelfLifeRow, load_reference_rows


MatchMethod = Literal["exact", "fuzzy", "unknown", "non_food"]


@dataclass(frozen=True)
class ProductMatch:
    """A food identity candidate without any storage recommendation.

    Only exact alias matches carry a canonical key.  A fuzzy match may provide a
    conservative category hint, but deliberately cannot unlock a row-specific
    shelf life in ``resolve_item``.
    """

    name: str
    canonical_key: str | None
    category: str
    is_perishable: bool
    confidence: float
    needs_review: bool
    method: MatchMethod


_TOKEN_EXPANSIONS = {
    "apl": "apple",
    "avos": "avocados",
    "ban": "banana",
    "bns": "beans",
    "bnls": "boneless",
    "brd": "bread",
    "brst": "breast",
    "chkn": "chicken",
    "dz": "dozen",
    "frz": "frozen",
    "grnd": "ground",
    "mlk": "milk",
    "oj": "orange juice",
    "org": "organic",
    "strawb": "strawberries",
    "veg": "vegetables",
    "whl": "whole",
    "yog": "yogurt",
}

_IGNORED_MODIFIERS = {
    "boneless",
    "brand",
    "fresh",
    "large",
    "medium",
    "organic",
    "small",
}

_NON_FOOD_PHRASES = (
    "aluminum foil",
    "batteries",
    "cleaner",
    "detergent",
    "dish soap",
    "facial tissue",
    "laundry soap",
    "paper plates",
    "paper towels",
    "pet food",
    "shampoo",
    "soap",
    "toilet paper",
    "trash bags",
)

_CONDITION_GROUPS = (
    {
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
    },
    {"raw", "uncooked"},
    {"opened"},
    {"unopened"},
    {"frozen"},
    {"dry", "dried"},
)

# These words describe a moist or prepared state that must never be erased when
# evaluating a dry pantry-staple candidate.  We still allow harmless display
# modifiers such as "fresh" to be stripped for fresh produce, meat, and bakery
# aliases; the original query is retained for this safety compatibility check.
_MOIST_OR_PREPARED_TOKENS = {
    "chilled",
    "cooked",
    "fresh",
    "leftover",
    "leftovers",
    "moist",
    "prepared",
    "refrigerated",
    "wet",
}


def normalize_product_text(value: str) -> str:
    """Normalize common OCR noise while keeping word boundaries deterministic."""

    value = re.sub(r"(?<=[A-Za-z])\$(?=\s|$)", "s", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().replace("&", " and ")
    tokens = re.sub(r"[^a-z0-9]+", " ", value).split()
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(_TOKEN_EXPANSIONS.get(token, token).split())
    return " ".join(expanded)


def _humanize(value: str) -> str:
    normalized = normalize_product_text(value)
    return normalized.title() if normalized else "Unknown item"


def _row_text(row: ShelfLifeRow) -> str:
    return " ".join(
        (
            row.canonical_key.replace("_", " "),
            row.display_name,
            *row.aliases,
            row.notes,
        )
    )


def _condition_is_compatible(query: str, row: ShelfLifeRow) -> bool:
    query_tokens = set(query.split())
    row_tokens = set(normalize_product_text(_row_text(row)).split())
    if (
        query_tokens & _MOIST_OR_PREPARED_TOKENS
        and row.category == "pantry_staple"
    ):
        return False
    return all(
        not (query_tokens & group) or bool(row_tokens & group)
        for group in _CONDITION_GROUPS
    )


def _is_perishable(row: ShelfLifeRow) -> bool:
    if row.category in {"pantry_staple", "non_food"}:
        return False
    if row.recommended_method in {"fridge", "freezer"}:
        return True
    return row.category in {"bakery", "dairy", "deli", "meat", "produce", "seafood"}


@dataclass(frozen=True)
class _AliasCandidate:
    phrase: str
    row: ShelfLifeRow


@lru_cache(maxsize=1)
def _alias_candidates() -> tuple[_AliasCandidate, ...]:
    candidates: dict[tuple[str, str], _AliasCandidate] = {}
    for row in load_reference_rows():
        phrases = (
            row.canonical_key.replace("_", " "),
            row.display_name,
            *row.aliases,
        )
        for phrase in phrases:
            normalized = normalize_product_text(phrase)
            if normalized:
                candidates[(normalized, row.canonical_key)] = _AliasCandidate(
                    normalized, row
                )
    return tuple(candidates.values())


def _is_phrase_in_query(phrase: str, query: str) -> bool:
    return f" {phrase} " in f" {query} "


def _exact_candidate(
    query: str, *, condition_query: str | None = None
) -> _AliasCandidate | None:
    condition_query = condition_query or query
    equal = [
        candidate
        for candidate in _alias_candidates()
        if candidate.phrase == query
        if _condition_is_compatible(condition_query, candidate.row)
    ]
    if not equal:
        return None
    rows = {candidate.row.canonical_key for candidate in equal}
    return equal[0] if len(rows) == 1 else None


def _fuzzy_candidate(
    query: str, *, condition_query: str | None = None
) -> tuple[_AliasCandidate, float] | None:
    if len(query) < 5:
        return None
    condition_query = condition_query or query
    try:
        from rapidfuzz.fuzz import ratio
    except ImportError:
        # A missing optional matcher must make the system abstain, never guess.
        return None

    scored: dict[str, tuple[float, _AliasCandidate]] = {}
    first = query[0]
    for candidate in _alias_candidates():
        if (
            len(candidate.phrase) < 5
            or candidate.phrase[0] != first
            or not _condition_is_compatible(condition_query, candidate.row)
        ):
            continue
        score = float(ratio(query, candidate.phrase))
        previous = scored.get(candidate.row.canonical_key)
        if previous is None or score > previous[0]:
            scored[candidate.row.canonical_key] = (score, candidate)

    ranked = sorted(scored.values(), key=lambda result: result[0], reverse=True)
    if not ranked or ranked[0][0] < 90:
        return None
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if ranked[0][0] - runner_up < 5:
        return None
    return ranked[0][1], ranked[0][0]


def _without_ignored_modifiers(query: str) -> str:
    tokens = [token for token in query.split() if token not in _IGNORED_MODIFIERS]
    return " ".join(tokens) or query


def match_product(name: str, *, ocr_confidence: float = 1.0) -> ProductMatch:
    """Match a receipt description to the local reference table conservatively."""

    confidence = max(0.0, min(1.0, ocr_confidence))
    query = normalize_product_text(name)
    compact_query = _without_ignored_modifiers(query)

    if any(_is_phrase_in_query(phrase, query) for phrase in _NON_FOOD_PHRASES):
        return ProductMatch(
            name=_humanize(name),
            canonical_key=None,
            category="non_food",
            is_perishable=False,
            confidence=confidence,
            needs_review=confidence < 0.75,
            method="non_food",
        )

    exact = _exact_candidate(query) or (
        _exact_candidate(compact_query, condition_query=query)
        if compact_query != query
        else None
    )
    if exact is not None:
        return ProductMatch(
            name=exact.row.display_name,
            canonical_key=exact.row.canonical_key,
            category=exact.row.category,
            is_perishable=_is_perishable(exact.row),
            confidence=confidence,
            needs_review=confidence < 0.75,
            method="exact",
        )

    fuzzy = _fuzzy_candidate(compact_query, condition_query=query)
    if fuzzy is not None:
        candidate, score = fuzzy
        # No canonical key: a spelling guess may hint at a conservative category,
        # but may not unlock item-specific shelf-life guidance.
        return ProductMatch(
            name=_humanize(name),
            canonical_key=None,
            category=candidate.row.category,
            is_perishable=_is_perishable(candidate.row),
            confidence=min(confidence, score / 100.0) * 0.85,
            needs_review=True,
            method="fuzzy",
        )

    return ProductMatch(
        name=_humanize(name),
        canonical_key=None,
        category="unknown",
        is_perishable=True,
        confidence=min(confidence, 0.5),
        needs_review=True,
        method="unknown",
    )


def clear_product_matcher_cache() -> None:
    _alias_candidates.cache_clear()
