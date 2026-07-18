from __future__ import annotations

import pytest

from app.services.local_ocr import OcrToken, parse_receipt_text, reconstruct_lines


def _token(
    text: str,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    confidence: float = 0.99,
) -> OcrToken:
    return OcrToken(
        text=text,
        box=((left, top), (right, top), (right, bottom), (left, bottom)),
        confidence=confidence,
    )


def test_spatial_line_reconstruction_joins_name_and_price() -> None:
    tokens = [
        _token("3.49", left=400, top=101, right=450, bottom=121, confidence=0.91),
        _token(
            "BABY SPINACH 5OZ",
            left=50,
            top=98,
            right=250,
            bottom=118,
            confidence=0.98,
        ),
        _token("WHOLE MILK", left=50, top=145, right=200, bottom=165),
        _token("4.29", left=400, top=147, right=450, bottom=167),
    ]

    lines = reconstruct_lines(tokens)
    assert [line.text for line in lines] == [
        "BABY SPINACH 5OZ 3.49",
        "WHOLE MILK 4.29",
    ]
    assert lines[0].confidence == 0.91
    assert [token.text for token in lines[0].tokens] == [
        "BABY SPINACH 5OZ",
        "3.49",
    ]
    assert lines[0].box == ((50, 98), (450, 98), (450, 121), (50, 121))


def test_pure_parser_reads_store_date_anchors_and_normal_items() -> None:
    parsed = parse_receipt_text(
        """
        FRESH BASKET MARKET
        DATE 07/18/2026 TIME 08:15 PM
        BABY SPINACH 5OZ          3.49
        WHOLE MILK 1GAL           4.29
        PAPER TOWELS 6PK          7.49
        SUBTOTAL                 $15.27
        SALES TAX                 $0.47
        GRAND TOTAL              $15.74
        """
    )

    assert parsed.store_name == "Fresh Basket Market"
    assert parsed.purchased_at == "2026-07-18"
    assert parsed.subtotal == 15.27
    assert parsed.tax == 0.47
    assert parsed.total == 15.74
    assert parsed.image_quality_issue is None
    assert len(parsed.items) == 3

    spinach, milk, towels = parsed.items
    assert spinach.canonical_key == "baby_spinach"
    assert spinach.qty == 1
    assert spinach.unit == "each"
    assert spinach.storage is None
    assert spinach.eat_by_window is None

    assert milk.canonical_key == "whole_milk"
    assert milk.qty == 1
    assert milk.unit == "gallon"
    assert milk.unit_price == 4.29

    assert towels.category == "non_food"
    assert towels.is_perishable is False
    assert towels.unit == "pack"


def test_parser_handles_at_xn_and_implicit_weighted_totals() -> None:
    parsed = parse_receipt_text(
        """
        TEST GROCER
        2 @ $1.50 APPLES          3.00
        CANNED BLACK BEANS X3     3.87
        BANANAS 1.25 LB @ $0.69/LB 0.86
        SALMON FILLET 1.18LB     14.15
        SUBTOTAL                 21.88
        TOTAL                    21.88
        """
    )

    assert len(parsed.items) == 4
    apples, beans, bananas, salmon = parsed.items

    assert (apples.qty, apples.unit, apples.unit_price, apples.line_total) == (
        2,
        "each",
        1.5,
        3.0,
    )
    assert (beans.qty, beans.unit, beans.unit_price, beans.line_total) == (
        3,
        "each",
        1.29,
        3.87,
    )
    assert (bananas.qty, bananas.unit, bananas.unit_price, bananas.line_total) == (
        1.25,
        "lb",
        0.69,
        0.86,
    )
    assert (salmon.qty, salmon.unit, salmon.unit_price, salmon.line_total) == (
        1.18,
        "lb",
        11.99,
        14.15,
    )


def test_weighted_at_line_without_printed_total_is_computed() -> None:
    parsed = parse_receipt_text(
        """
        TEST MARKET
        BANANAS 1.25 LB @ $0.69/LB
        """
    )

    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.qty == 1.25
    assert item.unit_price == 0.69
    assert item.line_total == 0.86


@pytest.mark.parametrize(
    "line",
    [
        "APPLES X0 3.00",
        "0X APPLES 3.00",
        "APPLES X999 3.00",
        "APPLES 0.0LB 3.00",
    ],
)
def test_invalid_quantity_never_divides_by_zero_and_requires_review(line: str) -> None:
    parsed = parse_receipt_text(f"TEST MARKET\n{line}\nTOTAL 3.00")
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.qty == 1
    assert item.unit_price == 3.00
    assert item.line_total == 3.00
    assert item.needs_review is True


@pytest.mark.parametrize("line", ["TIP 3.00", "BALANCE 1.00"])
def test_payment_summary_lines_are_not_food_items(line: str) -> None:
    parsed = parse_receipt_text(f"TEST MARKET\n{line}")
    assert parsed.items == []


def test_arbitrary_text_plus_price_is_not_accepted_as_a_receipt() -> None:
    parsed = parse_receipt_text("HELLO WORLD 3.99")
    assert parsed.image_quality_issue == "not_a_receipt"
    assert parsed.overall_confidence < 0.5


def test_cropped_multi_item_receipt_is_preserved_but_forces_review() -> None:
    parsed = parse_receipt_text(
        "TEST MARKET\nAPPLES 1.00\nWHOLE MILK 4.29"
    )
    assert parsed.image_quality_issue == "cropped"
    assert len(parsed.items) == 2
    assert all(item.needs_review for item in parsed.items)


def test_discount_is_negative_non_food_row_and_unknown_is_conservative() -> None:
    parsed = parse_receipt_text(
        """
        TEST MARKET
        MYSTERY FARM BOX          8.00
        STORE COUPON             -1.00
        SUBTOTAL                  7.00
        TOTAL                     7.00
        TOTAL SAVINGS             1.00
        CASH                     10.00
        """
    )

    unknown, discount = parsed.items
    assert unknown.category == "unknown"
    assert unknown.is_perishable is True
    assert unknown.canonical_key is None
    assert unknown.needs_review is True
    assert unknown.storage is None
    assert unknown.eat_by_window is None

    assert discount.category == "non_food"
    assert discount.is_perishable is False
    assert discount.line_total == -1.0
    assert discount.storage is None
    assert parsed.total == 7.0


@pytest.mark.parametrize(
    ("raw_date", "expected"),
    [
        ("DATE .07/12/2026", "2026-07-12"),
        ("2026-07-12 18:44", "2026-07-12"),
        ("Jul 12, 2026", "2026-07-12"),
    ],
)
def test_date_formats(raw_date: str, expected: str) -> None:
    parsed = parse_receipt_text(f"TEST MARKET\n{raw_date}\nAPPLE 1.00")
    assert parsed.purchased_at == expected
