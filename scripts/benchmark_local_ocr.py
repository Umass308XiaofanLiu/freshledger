"""Run the zero-cloud receipt recognizer against the committed fixtures.

This is an engineering gate, not a claim about arbitrary real-world receipts.
It measures item capture, canonical matching, exact prices, reconciliation, and
latency for the three standing Build Week samples.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "server"
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "receipts"
SAMPLE_IDS = ("r1", "r2", "r3")
MIN_ITEM_RATE = 0.90
MIN_PRICE_RATE = 0.90

sys.path.insert(0, str(SERVER_ROOT))

from app.services.images import prepare_receipt_image  # noqa: E402
from app.services.local_ocr import parse_local_receipt_image  # noqa: E402
from app.services.receipt_pipeline import reconcile  # noqa: E402
from app.services.shelf_life import resolve_item  # noqa: E402


async def run() -> int:
    summaries: list[dict[str, object]] = []
    gate_passed = True

    for sample_id in SAMPLE_IDS:
        expected = json.loads(
            (FIXTURE_ROOT / "expected" / f"{sample_id}.json").read_text(
                encoding="utf-8"
            )
        )
        image = prepare_receipt_image(
            (FIXTURE_ROOT / f"{sample_id}.jpg").read_bytes()
        )
        started = perf_counter()
        parsed = await parse_local_receipt_image(image)
        elapsed_seconds = perf_counter() - started

        expected_items = expected["items"]
        compared = min(len(expected_items), len(parsed.items))
        canonical_matches = sum(
            parsed.items[index].canonical_key
            == expected_items[index]["canonical_key"]
            for index in range(compared)
        )
        exact_prices = sum(
            round(parsed.items[index].line_total, 2)
            == round(expected_items[index]["line_total"], 2)
            for index in range(compared)
        )
        category_matches = sum(
            parsed.items[index].category == expected_items[index]["category"]
            for index in range(compared)
        )
        quantity_matches = sum(
            round(parsed.items[index].qty, 3)
            == round(expected_items[index]["qty"], 3)
            for index in range(compared)
        )
        exact_item_count = len(parsed.items) == len(expected_items)
        item_rate = max(
            0.0,
            1.0
            - abs(len(parsed.items) - len(expected_items))
            / max(1, len(expected_items)),
        )
        price_rate = exact_prices / max(1, len(expected_items))
        resolved_items = [
            resolve_item(item, item_id=index)
            for index, item in enumerate(parsed.items, start=1)
        ]
        reconciliation = reconcile(parsed, resolved_items)
        safety_checks = []
        grounded_reference = 0
        for item in resolved_items:
            if item.category == "non_food":
                safety_checks.append(
                    item.excluded
                    and item.storage is None
                    and item.eat_by_window is None
                    and item.shelf_life_source is None
                )
                continue
            is_grounded = (
                not item.excluded
                and item.canonical_key is not None
                and item.storage is not None
                and item.shelf_life_source == "reference"
                and 1 <= item.storage.duration_days <= 730
                and (
                    item.eat_by_window is None
                    or item.eat_by_window.end_days <= item.storage.duration_days
                )
            )
            grounded_reference += int(is_grounded)
            safety_checks.append(is_grounded)
        review_count = sum(item.needs_review for item in parsed.items)
        sample_passed = (
            exact_item_count
            and item_rate >= MIN_ITEM_RATE
            and price_rate >= MIN_PRICE_RATE
            and canonical_matches == len(expected_items)
            and category_matches == len(expected_items)
            and quantity_matches == len(expected_items)
            and review_count == 0
            and all(safety_checks)
            and reconciliation.status == "ok"
        )
        gate_passed = gate_passed and sample_passed
        summaries.append(
            {
                "sample": sample_id,
                "passed": sample_passed,
                "expected_items": len(expected_items),
                "parsed_items": len(parsed.items),
                "item_capture_rate": round(item_rate, 3),
                "canonical_matches": canonical_matches,
                "category_matches": category_matches,
                "quantity_matches": quantity_matches,
                "exact_price_rate": round(price_rate, 3),
                "reconciliation": reconciliation.status,
                "delta": reconciliation.delta,
                "grounded_reference_food_items": grounded_reference,
                "safety_checks_passed": all(safety_checks),
                "needs_review": review_count,
                "elapsed_seconds": round(elapsed_seconds, 3),
            }
        )

    print(json.dumps({"passed": gate_passed, "samples": summaries}, indent=2))
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
