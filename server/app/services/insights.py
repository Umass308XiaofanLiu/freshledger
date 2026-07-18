from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ..db import connect, init_database
from ..models import (
    BuyingAdvice,
    CategorySpend,
    DemoGeneration,
    InsightsPeriod,
    InsightsResponse,
    InsightTotals,
    WasteEventInsight,
)


def _money(cents: int) -> float:
    return float(
        (Decimal(cents) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def _advice(connection) -> list[BuyingAdvice]:
    advice: list[BuyingAdvice] = []
    top_waste = connection.execute(
        """
        SELECT
          COALESCE(p.canonical_key, lower(p.name)) AS canonical_key,
          MIN(p.name) AS name,
          SUM(w.cost_lost_cents) AS wasted_cents
        FROM waste_events w
        JOIN pantry_items p ON p.id = w.pantry_item_id
        GROUP BY COALESCE(p.canonical_key, lower(p.name))
        HAVING SUM(w.cost_lost_cents) > 0
        ORDER BY wasted_cents DESC, canonical_key
        LIMIT 1
        """
    ).fetchone()
    if top_waste is not None:
        wasted = _money(int(top_waste["wasted_cents"]))
        advice.append(
            BuyingAdvice(
                kind="buy_less",
                canonical_key=str(top_waste["canonical_key"]),
                text=(
                    f"You tracked ${wasted:.2f} of {top_waste['name']} waste. "
                    "Try a smaller quantity next time and use the oldest package first."
                ),
            )
        )

    category_rows = connection.execute(
        """
        SELECT ri.category, SUM(ri.line_total_cents) AS spent_cents
        FROM receipt_items ri
        JOIN receipts r ON r.id = ri.receipt_id
        WHERE r.status = 'confirmed' AND ri.excluded = 0 AND ri.category != 'non_food'
        GROUP BY ri.category
        ORDER BY spent_cents DESC, ri.category
        LIMIT 3
        """
    ).fetchall()
    for row in category_rows:
        spent = _money(int(row["spent_cents"]))
        category = str(row["category"])
        advice.append(
            BuyingAdvice(
                kind="well_bought",
                canonical_key=category,
                text=(
                    f"Tracked {category} spending is ${spent:.2f}. "
                    "Check active stock before adding more on the next trip."
                ),
            )
        )
        if len(advice) == 5:
            return advice

    totals = connection.execute(
        """
        SELECT
          COUNT(*) AS receipt_count,
          COALESCE(SUM(COALESCE(total_cents, computed_sum_cents)), 0) AS spent_cents
        FROM receipts
        WHERE status = 'confirmed'
        """
    ).fetchone()
    receipt_count = int(totals["receipt_count"])
    if receipt_count == 0:
        return [
            BuyingAdvice(
                kind="well_bought",
                canonical_key="insufficient_history",
                text="Confirm a receipt to unlock data-backed buy-smarter tips.",
            )
        ]

    food_spent_cents = int(
        connection.execute(
            """
            SELECT COALESCE(SUM(ri.line_total_cents), 0)
            FROM receipt_items ri
            JOIN receipts r ON r.id = ri.receipt_id
            WHERE r.status = 'confirmed' AND ri.excluded = 0
              AND ri.category != 'non_food'
            """
        ).fetchone()[0]
    )
    waste_cents = int(
        connection.execute(
            "SELECT COALESCE(SUM(cost_lost_cents), 0) FROM waste_events"
        ).fetchone()[0]
    )
    fallback_rows = (
        (
            "overall_spend",
            f"You tracked ${_money(int(totals['spent_cents'])):.2f} across "
            f"{receipt_count} confirmed receipt{'s' if receipt_count != 1 else ''}. "
            "Review the active pantry before the next shop.",
        ),
        (
            "food_spend",
            f"Confirmed food line items total ${_money(food_spent_cents):.2f}. "
            "Plan the next list from what remains active.",
        ),
        (
            "waste_history",
            f"Recorded waste totals ${_money(waste_cents):.2f}. "
            "Keep using oldest tracked items first.",
        ),
    )
    for canonical_key, text in fallback_rows:
        if len(advice) >= 3:
            break
        advice.append(
            BuyingAdvice(
                kind="well_bought",
                canonical_key=canonical_key,
                text=text,
            )
        )
    return advice[:5]


def get_insights(
    *,
    today: date | None = None,
    database_path: str | Path | None = None,
) -> InsightsResponse:
    """Compute money and waste facts from SQLite without AI narration."""

    as_of = today or date.today()
    init_database(database_path)
    with connect(database_path) as connection:
        receipt_totals = connection.execute(
            """
            SELECT
              COUNT(*) AS receipt_count,
              MIN(purchased_at) AS first_purchase,
              COALESCE(SUM(COALESCE(total_cents, computed_sum_cents)), 0) AS spent_cents
            FROM receipts
            WHERE status = 'confirmed'
            """
        ).fetchone()
        food_row = connection.execute(
            """
            SELECT COALESCE(SUM(ri.line_total_cents), 0) AS food_spent_cents
            FROM receipt_items ri
            JOIN receipts r ON r.id = ri.receipt_id
            WHERE r.status = 'confirmed' AND ri.excluded = 0 AND ri.category != 'non_food'
            """
        ).fetchone()
        waste_row = connection.execute(
            "SELECT COALESCE(SUM(cost_lost_cents), 0) AS wasted_cents FROM waste_events"
        ).fetchone()
        category_rows = connection.execute(
            """
            SELECT ri.category, SUM(ri.line_total_cents) AS spent_cents
            FROM receipt_items ri
            JOIN receipts r ON r.id = ri.receipt_id
            WHERE r.status = 'confirmed' AND ri.excluded = 0 AND ri.category != 'non_food'
            GROUP BY ri.category
            ORDER BY spent_cents DESC, ri.category
            """
        ).fetchall()
        waste_rows = connection.execute(
            """
            SELECT p.name, w.occurred_at, w.cost_lost_cents
            FROM waste_events w
            JOIN pantry_items p ON p.id = w.pantry_item_id
            ORDER BY w.occurred_at DESC, w.id DESC
            """
        ).fetchall()
        advice = _advice(connection)

    spent_cents = int(receipt_totals["spent_cents"])
    food_spent_cents = int(food_row["food_spent_cents"])
    wasted_cents = int(waste_row["wasted_cents"])
    waste_rate = (
        float(
            (Decimal(wasted_cents) / Decimal(spent_cents)).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
        )
        if spent_cents > 0 and wasted_cents > 0
        else 0.0
    )
    start = receipt_totals["first_purchase"] or as_of.isoformat()

    return InsightsResponse(
        period=InsightsPeriod(**{"from": start, "to": as_of.isoformat()}),
        totals=InsightTotals(
            spent=_money(spent_cents),
            food_spent=_money(food_spent_cents),
            wasted=_money(wasted_cents),
            waste_rate=waste_rate,
            receipt_count=int(receipt_totals["receipt_count"]),
        ),
        by_category=[
            CategorySpend(
                category=str(row["category"]),
                spent=_money(int(row["spent_cents"])),
            )
            for row in category_rows
        ],
        waste_events=[
            WasteEventInsight(
                name=str(row["name"]),
                occurred_at=str(row["occurred_at"])[:10],
                cost_lost=_money(int(row["cost_lost_cents"])),
            )
            for row in waste_rows
        ],
        advice=advice,
        generation=DemoGeneration(),
    )
