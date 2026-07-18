from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import connect, init_database
from app.main import app
from app.services.demo_data import load_demo_parse
from app.services.meals import get_meals_today
from app.services.receipt_store import consume_pantry_item, spoil_pantry_item
from app.services.receipt_store import confirm_receipt, persist_receipt_draft
from app.services.shelf_life import resolve_item


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "test-demo-token")
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(tmp_path / "freshledger.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _seed_confirmed_history(path: Path) -> dict[str, int]:
    today = date.today()
    init_database(path)
    with connect(path) as connection, connection:
        receipt_id = int(
            connection.execute(
                """
                INSERT INTO receipts (
                  store_name, purchased_at, total_cents, computed_sum_cents,
                  reconciliation_status, status
                ) VALUES ('Demo Market', ?, 2500, 2500, 'ok', 'confirmed')
                """,
                ((today - timedelta(days=1)).isoformat(),),
            ).lastrowid
        )
        item_specs = (
            ("Spinach", "spinach", "produce", 200, 200, 0),
            ("Yogurt", "yogurt", "dairy", 300, 300, 0),
            ("Bread", "bread", "bakery", 400, 400, 0),
            ("Chicken", "chicken_raw", "meat", 1000, 1000, 0),
            ("Paper towels", None, "non_food", 600, 600, 1),
        )
        receipt_item_ids: list[int] = []
        for name, key, category, unit_cents, line_cents, excluded in item_specs:
            receipt_item_ids.append(
                int(
                    connection.execute(
                        """
                        INSERT INTO receipt_items (
                          receipt_id, raw_text, name, canonical_key, qty, unit,
                          unit_price_cents, line_total_cents, category,
                          is_perishable, confidence, needs_review, excluded
                        ) VALUES (?, ?, ?, ?, 1, 'each', ?, ?, ?, ?, 1, 0, ?)
                        """,
                        (
                            receipt_id,
                            name.upper(),
                            name,
                            key,
                            unit_cents,
                            line_cents,
                            category,
                            int(category != "non_food"),
                            excluded,
                        ),
                    ).lastrowid
                )
            )

        pantry: dict[str, int] = {}
        pantry_specs = (
            ("spinach", 0, 1),
            ("yogurt", 1, 5),
            ("bread", 2, 4),
            ("chicken", 3, 2),
        )
        for label, receipt_index, days_left in pantry_specs:
            name, key, category, unit_cents, _, _ = item_specs[receipt_index]
            pantry[label] = int(
                connection.execute(
                    """
                    INSERT INTO pantry_items (
                      receipt_item_id, name, canonical_key, category, qty_initial,
                      qty_remaining, unit, unit_price_cents, storage_method,
                      storage_temp_c, storage_duration_days, shelf_life_source,
                      purchased_at, best_by, safe_until, status
                    ) VALUES (?, ?, ?, ?, 1, 1, 'each', ?, 'fridge', 4, 7,
                              'reference', ?, ?, ?, 'active')
                    """,
                    (
                        receipt_item_ids[receipt_index],
                        name,
                        key,
                        category,
                        unit_cents,
                        (today - timedelta(days=1)).isoformat(),
                        (today + timedelta(days=days_left)).isoformat(),
                        (today + timedelta(days=7)).isoformat(),
                    ),
                ).lastrowid
            )

        pantry["expired"] = int(
            connection.execute(
                """
                INSERT INTO pantry_items (
                  name, canonical_key, category, qty_initial, qty_remaining, unit,
                  unit_price_cents, storage_method, storage_temp_c,
                  storage_duration_days, shelf_life_source, purchased_at, best_by,
                  safe_until, status
                ) VALUES ('Expired berries', 'berries', 'produce', 1, 1, 'each',
                          500, 'fridge', 4, 3, 'reference', ?, ?, ?, 'active')
                """,
                (
                    (today - timedelta(days=5)).isoformat(),
                    (today - timedelta(days=2)).isoformat(),
                    (today - timedelta(days=2)).isoformat(),
                ),
            ).lastrowid
        )
        pantry["unsafe"] = int(
            connection.execute(
                """
                INSERT INTO pantry_items (
                  name, canonical_key, category, qty_initial, qty_remaining, unit,
                  unit_price_cents, storage_method, storage_temp_c,
                  storage_duration_days, shelf_life_source, purchased_at, best_by,
                  safe_until, status
                ) VALUES ('Inconsistent unsafe item', 'unsafe', 'unknown', 1, 1,
                          'each', 900, 'fridge', 4, 3, 'reference', ?, ?, ?, 'active')
                """,
                (
                    today.isoformat(),
                    (today + timedelta(days=2)).isoformat(),
                    (today - timedelta(days=1)).isoformat(),
                ),
            ).lastrowid
        )
    return pantry


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-demo-token"}


def _seed_r2(path: Path) -> None:
    parsed = load_demo_parse("r2")
    preliminary = [
        resolve_item(item, item_id=index)
        for index, item in enumerate(parsed.items, start=1)
    ]
    persisted = persist_receipt_draft(
        parsed,
        image_hash="r2-meal-template",
        resolved_items=preliminary,
        database_path=path,
    )
    resolved = [
        resolve_item(item, item_id=item_id)
        for item, item_id in zip(parsed.items, persisted.item_ids, strict=True)
    ]
    confirm_receipt(
        persisted.receipt_id,
        resolved,
        database_path=path,
    )


def test_meals_are_deterministic_grounded_and_cached(
    client: TestClient, tmp_path: Path
) -> None:
    pantry = _seed_confirmed_history(tmp_path / "freshledger.db")

    first = client.get("/v1/meals/today", headers=_auth())
    second = client.get("/v1/meals/today", headers=_auth())

    assert first.status_code == 200
    payload = first.json()
    assert payload["generation"] == {
        "mode": "demo",
        "ai_called": False,
        "method": "deterministic",
    }
    assert payload["cached"] is False
    assert second.json()["cached"] is True
    assert len(payload["meals"]) == 3
    assert all(
        meal["label"] == "Deterministic demo suggestion"
        for meal in payload["meals"]
    )
    urgent_ids = {pantry["spinach"], pantry["chicken"]}
    for meal in payload["meals"]:
        use_ids = {item["pantry_item_id"] for item in meal["uses"]}
        assert use_ids & urgent_ids
        assert pantry["expired"] not in use_ids
        assert pantry["unsafe"] not in use_ids
        assert len(meal["steps"]) <= 3
        assert "$" in meal["why_now"]
        assert "safe internal temperature" in meal["safety_note"]

    chicken_meals = [
        meal
        for meal in payload["meals"]
        if pantry["chicken"]
        in {item["pantry_item_id"] for item in meal["uses"]}
    ]
    assert chicken_meals
    assert all(meal["time_minutes"] == 40 for meal in chicken_meals)

    refreshed = client.get("/v1/meals/today?refresh=1", headers=_auth())
    assert refreshed.status_code == 200
    assert refreshed.json()["cached"] is False
    assert refreshed.json()["meals"] == payload["meals"]

    consume_pantry_item(
        pantry["chicken"], database_path=tmp_path / "freshledger.db"
    )
    after_consume = client.get("/v1/meals/today", headers=_auth())
    assert after_consume.status_code == 200
    assert after_consume.json()["cached"] is False
    assert all(
        pantry["chicken"]
        not in {item["pantry_item_id"] for item in meal["uses"]}
        for meal in after_consume.json()["meals"]
    )


def test_r2_uses_credible_templates_and_never_cooks_a_beverage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r2.db"
    _seed_r2(path)

    response = get_meals_today(database_path=path)
    assert [meal.name for meal in response.meals] == [
        "Orange-Berry Yogurt Smoothie",
        "Egg, Black Bean & Rice Bowl",
        "Berry-Yogurt Breakfast",
    ]

    uses_by_title = {
        meal.name: {use.name for use in meal.uses} for meal in response.meals
    }
    assert uses_by_title["Orange-Berry Yogurt Smoothie"] == {
        "Orange juice",
        "Frozen mixed berries",
        "Plain Greek yogurt",
    }
    assert uses_by_title["Egg, Black Bean & Rice Bowl"] == {
        "Large eggs",
        "Canned black beans",
        "Long-grain rice",
        "Orange juice",
    }
    assert uses_by_title["Berry-Yogurt Breakfast"] == {
        "Frozen mixed berries",
        "Plain Greek yogurt",
        "Orange juice",
    }

    banned_beverage_titles = ("Quick skillet", "Warm bowl", "Simple tray")
    for meal in response.meals:
        assert not any(
            meal.name.lower().startswith(title.lower())
            or f"orange juice {title}" in meal.name.lower()
            for title in banned_beverage_titles
        )
        assert len(meal.steps) <= 3
        assert "package directions" in meal.safety_note
        assert any(use.name == "Orange juice" and use.days_left <= 3 for use in meal.uses)
        if "Smoothie" not in meal.name:
            assert any(
                "Orange juice" in step and "side" in step and "do not cook" in step
                for step in meal.steps
            )


def test_insights_use_exact_sql_money_and_deterministic_advice(
    client: TestClient, tmp_path: Path
) -> None:
    pantry = _seed_confirmed_history(tmp_path / "freshledger.db")
    mutation = spoil_pantry_item(
        pantry["spinach"], 0.5, database_path=tmp_path / "freshledger.db"
    )
    assert mutation.cost_lost_cents == 100

    response = client.get("/v1/insights", headers=_auth())

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation"] == {
        "mode": "demo",
        "ai_called": False,
        "method": "deterministic",
    }
    assert payload["totals"] == {
        "spent": 25.0,
        "food_spent": 19.0,
        "wasted": 1.0,
        "waste_rate": 0.04,
        "receipt_count": 1,
    }
    assert payload["by_category"] == [
        {"category": "meat", "spent": 10.0},
        {"category": "bakery", "spent": 4.0},
        {"category": "dairy", "spent": 3.0},
        {"category": "produce", "spent": 2.0},
    ]
    assert payload["waste_events"][0]["name"] == "Spinach"
    assert payload["waste_events"][0]["cost_lost"] == 1.0
    assert payload["advice"][0]["kind"] == "buy_less"
    assert payload["advice"][0]["canonical_key"] == "spinach"
    assert "$1.00" in payload["advice"][0]["text"]
    assert payload["advice"][0]["label"] == "Deterministic demo advice"
    assert 3 <= len(payload["advice"]) <= 5


def test_consume_meal_is_atomic_and_rolls_back_on_any_invalid_item(
    client: TestClient, tmp_path: Path
) -> None:
    pantry = _seed_confirmed_history(tmp_path / "freshledger.db")

    failed = client.post(
        "/v1/meals/consume",
        headers=_auth(),
        json={
            "items": [
                {"pantry_item_id": pantry["spinach"], "portion": 1},
                {"pantry_item_id": 999_999, "portion": 1},
            ]
        },
    )
    assert failed.status_code == 404
    assert "nothing was changed" in failed.json()["error"]["user_message"]
    with connect(tmp_path / "freshledger.db") as connection:
        unchanged = connection.execute(
            "SELECT qty_remaining, status FROM pantry_items WHERE id = ?",
            (pantry["spinach"],),
        ).fetchone()
    assert unchanged["qty_remaining"] == 1
    assert unchanged["status"] == "active"

    succeeded = client.post(
        "/v1/meals/consume",
        headers=_auth(),
        json={
            "items": [
                {"pantry_item_id": pantry["spinach"]},
                {"pantry_item_id": pantry["yogurt"], "portion": 0.5},
            ]
        },
    )
    assert succeeded.status_code == 200
    assert succeeded.json() == {
        "consumed": [
            {
                "pantry_item_id": pantry["spinach"],
                "qty_remaining": 0.0,
                "status": "eaten",
            },
            {
                "pantry_item_id": pantry["yogurt"],
                "qty_remaining": 0.5,
                "status": "active",
            },
        ],
        "generation": {
            "mode": "demo",
            "ai_called": False,
            "method": "deterministic",
        },
    }


def test_consume_meal_rejects_duplicate_ids_without_writes(
    client: TestClient, tmp_path: Path
) -> None:
    pantry = _seed_confirmed_history(tmp_path / "freshledger.db")
    response = client.post(
        "/v1/meals/consume",
        headers=_auth(),
        json={
            "items": [
                {"pantry_item_id": pantry["bread"], "portion": 0.25},
                {"pantry_item_id": pantry["bread"], "portion": 0.25},
            ]
        },
    )
    assert response.status_code == 422
    with connect(tmp_path / "freshledger.db") as connection:
        unchanged = connection.execute(
            "SELECT qty_remaining FROM pantry_items WHERE id = ?",
            (pantry["bread"],),
        ).fetchone()
    assert unchanged["qty_remaining"] == 1


@pytest.mark.parametrize("path", ["/v1/meals/today", "/v1/insights"])
def test_demo_intelligence_endpoints_require_auth(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_empty_history_returns_safe_empty_state(client: TestClient) -> None:
    meals = client.get("/v1/meals/today", headers=_auth())
    insights = client.get("/v1/insights", headers=_auth())

    assert meals.status_code == 200
    assert meals.json()["meals"] == []
    assert insights.status_code == 200
    assert insights.json()["totals"]["spent"] == 0
    assert insights.json()["totals"]["waste_rate"] == 0
    assert insights.json()["advice"][0]["canonical_key"] == "insufficient_history"
