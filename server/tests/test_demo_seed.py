from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import connect
from app.main import app


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
        assert [receipt["receipt_id"] for receipt in ledger["receipts"]] == [3, 2, 1]
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
