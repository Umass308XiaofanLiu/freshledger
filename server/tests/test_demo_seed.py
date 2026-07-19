from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import connect
from app.main import app
from app.services.receipt_store import find_product_alias, upsert_product_alias


@pytest.fixture
def configured_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    database_path = tmp_path / "seed.db"
    monkeypatch.setenv("DEMO_TOKEN", "seed-demo-token")
    monkeypatch.setenv("ADMIN_TOKEN", "seed-admin-token")
    monkeypatch.setenv("FRESHLEDGER_DB_PATH", str(database_path))
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "false")
    get_settings.cache_clear()
    yield database_path
    get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer seed-demo-token"}


def test_seed_endpoint_is_zero_token_repeatable_and_self_healing(
    configured_seed: Path,
) -> None:
    with TestClient(app) as client:
        first = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "judge"},
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload == {
            "seeded": True,
            "receipts": 3,
            "pantry_items": 19,
            "waste_events": 0,
            "generation": {
                "mode": "demo",
                "ai_called": False,
                "method": "deterministic",
            },
        }
        ledger = client.get("/v1/receipts", headers=_auth()).json()
        assert ledger["summary"]["receipt_count"] == 3
        assert [
            (receipt["receipt_id"], receipt["purchased_at"])
            for receipt in ledger["receipts"]
        ] == [
            (1, "2026-07-19"),
            (3, "2026-07-16"),
            (2, "2026-07-14"),
        ]
        pantry = client.get("/v1/pantry", headers=_auth()).json()
        assert len(pantry["items"]) == 19

        spoiled_id = pantry["items"][0]["pantry_item_id"]
        spoiled = client.post(
            f"/v1/pantry/{spoiled_id}/spoil",
            headers=_auth(),
            json={"portion": 1},
        )
        assert spoiled.status_code == 200
        assert spoiled.json()["waste_total_to_date"] > 0

        second = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "judge"},
        )
        assert second.status_code == 200
        assert second.json() == first_payload
        assert client.get("/v1/pantry", headers=_auth()).json()["items"][0][
            "pantry_item_id"
        ] == pantry["items"][0]["pantry_item_id"]
        insights = client.get("/v1/insights", headers=_auth()).json()
        assert insights["totals"]["wasted"] == 0
        assert 3 <= len(insights["advice"]) <= 5

    with connect(configured_seed) as connection:
        statuses = connection.execute(
            "SELECT status, COUNT(*) AS count FROM receipts GROUP BY status"
        ).fetchall()
        cached_meals = connection.execute(
            "SELECT COUNT(*) AS count FROM meal_suggestions"
        ).fetchone()["count"]
    assert [(row["status"], row["count"]) for row in statuses] == [
        ("confirmed", 3)
    ]
    assert cached_meals == 1


def test_seed_requires_demo_token_but_not_admin(configured_seed: Path) -> None:
    with TestClient(app) as client:
        missing = client.post("/v1/demo/seed", json={"profile": "judge"})
        assert missing.status_code == 401

        seeded = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "judge"},
        )
        assert seeded.status_code == 200

        invalid = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "not-judge"},
        )
        assert invalid.status_code == 422


def test_empty_database_is_auto_seeded_on_startup(
    configured_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "true")
    get_settings.cache_clear()

    with TestClient(app) as client:
        ledger = client.get("/v1/receipts", headers=_auth())
        pantry = client.get("/v1/pantry", headers=_auth())
        assert ledger.status_code == 200
        assert ledger.json()["summary"]["receipt_count"] == 3
        assert len(pantry.json()["items"]) == 19

    with connect(configured_seed) as connection:
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 3


def test_startup_does_not_replace_nonempty_database(
    configured_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        first = client.post(
            "/v1/demo/receipts/r1/scan",
            headers=_auth(),
        )
        assert first.status_code == 201
        draft_id = first.json()["receipt_id"]

    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "true")
    get_settings.cache_clear()
    with TestClient(app):
        pass
    with connect(configured_seed) as connection:
        rows = connection.execute(
            "SELECT id, status FROM receipts ORDER BY id"
        ).fetchall()
    assert [(row["id"], row["status"]) for row in rows] == [(draft_id, "draft")]


def test_user_clear_persistently_suppresses_startup_auto_seed(
    configured_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "true")
    get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/v1/receipts", headers=_auth()).json()["summary"][
            "receipt_count"
        ] == 3
        cleared = client.post(
            "/v1/demo/clear",
            headers=_auth(),
            json={"confirmation": "RESET_ALL_DATA"},
        )
        assert cleared.status_code == 200

    with TestClient(app) as restarted:
        ledger = restarted.get("/v1/receipts", headers=_auth()).json()
        assert ledger["summary"]["receipt_count"] == 0

    with connect(configured_seed) as connection:
        marker = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'demo_auto_seed_suppressed'"
        ).fetchone()
    assert marker is not None
    assert marker["value"] == "1"


def test_explicit_seed_clears_user_auto_seed_suppression(
    configured_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "true")
    get_settings.cache_clear()

    with TestClient(app) as client:
        cleared = client.post(
            "/v1/demo/clear",
            headers=_auth(),
            json={"confirmation": "RESET_ALL_DATA"},
        )
        assert cleared.status_code == 200
        seeded = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "judge"},
        )
        assert seeded.status_code == 200
        assert seeded.json()["receipts"] == 3

    with connect(configured_seed) as connection:
        marker = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'demo_auto_seed_suppressed'"
        ).fetchone()
    assert marker is None


def test_admin_reset_restores_configured_auto_seed_behavior(
    configured_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_SEED_DEMO_DATA", "true")
    get_settings.cache_clear()

    with TestClient(app) as client:
        cleared = client.post(
            "/v1/demo/clear",
            headers=_auth(),
            json={"confirmation": "RESET_ALL_DATA"},
        )
        assert cleared.status_code == 200
        reset = client.post(
            "/v1/demo/reset",
            headers={**_auth(), "X-Admin-Token": "seed-admin-token"},
        )
        assert reset.status_code == 200

    with TestClient(app) as restarted:
        ledger = restarted.get("/v1/receipts", headers=_auth()).json()
        assert ledger["summary"]["receipt_count"] == 3


def test_explicit_seed_preserves_confirmed_product_aliases(
    configured_seed: Path,
) -> None:
    upsert_product_alias(
        "ORG BABY SPIN 5OZ",
        canonical_key="spinach",
        display_name="Spinach",
        category="produce",
        is_perishable=True,
        merchant_name="Fixture Market",
    )

    with TestClient(app) as client:
        seeded = client.post(
            "/v1/demo/seed",
            headers=_auth(),
            json={"profile": "judge"},
        )
        assert seeded.status_code == 200

    alias = find_product_alias(
        "ORG BABY SPIN 5OZ", merchant_name="Fixture Market"
    )
    assert alias is not None
    assert alias.canonical_key == "spinach"
