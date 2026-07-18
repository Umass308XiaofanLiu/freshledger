from __future__ import annotations

import builtins

import pytest

from app.services.product_matcher import match_product, normalize_product_text


def test_exact_alias_match_tolerates_receipt_ocr_noise() -> None:
    assert normalize_product_text("BANAÑAS") == "bananas"
    assert normalize_product_text("CANNED BLACK BEAN$") == "canned black beans"

    bananas = match_product("BANAÑAS", ocr_confidence=0.96)
    assert bananas.method == "exact"
    assert bananas.canonical_key == "banana"
    assert bananas.category == "produce"
    assert bananas.is_perishable is True
    assert bananas.needs_review is False


def test_exact_alias_match_expands_common_grocery_abbreviations() -> None:
    chicken = match_product("BNLS CHKN BRST", ocr_confidence=0.92)
    assert chicken.method == "exact"
    assert chicken.canonical_key == "raw_chicken_breast"
    assert chicken.category == "meat"


def test_fuzzy_match_never_unlocks_a_reference_canonical_key() -> None:
    product = match_product("chiken breast", ocr_confidence=0.9)
    assert product.method == "fuzzy"
    assert product.canonical_key is None
    assert product.category == "meat"
    assert product.needs_review is True


def test_missing_rapidfuzz_abstains_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_without_rapidfuzz(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("rapidfuzz"):
            raise ImportError("simulated optional dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_rapidfuzz)
    product = match_product("chiken breast")
    assert product.method == "unknown"
    assert product.canonical_key is None
    assert product.category == "unknown"
    assert product.is_perishable is True
    assert product.needs_review is True


@pytest.mark.parametrize(
    "name",
    [
        "Cooked rice",
        "Fresh pasta",
        "Fresh rice",
        "Refrigerated rice",
        "Rice pudding",
        "Pasta salad",
        "Wet rice",
    ],
)
def test_prepared_food_cannot_match_a_dry_ingredient(name: str) -> None:
    product = match_product(name)
    assert product.canonical_key is None
    assert product.category == "unknown"
    assert product.is_perishable is True
    assert product.needs_review is True


@pytest.mark.parametrize(
    "name",
    [
        "Apple juice",
        "Chicken sandwich",
        "Milk chocolate",
        "Salmon dog food",
    ],
)
def test_contained_food_word_never_counts_as_an_exact_alias(name: str) -> None:
    product = match_product(name)
    assert product.method != "exact"
    assert product.canonical_key is None
    assert product.needs_review is True


def test_non_food_keywords_are_excluded_from_food_grounding() -> None:
    product = match_product("PAPER TOWELS")
    assert product.method == "non_food"
    assert product.canonical_key is None
    assert product.category == "non_food"
    assert product.is_perishable is False
