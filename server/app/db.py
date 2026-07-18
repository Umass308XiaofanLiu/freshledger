from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
  id                    INTEGER PRIMARY KEY,
  store_name            TEXT,
  purchased_at          TEXT NOT NULL,
  image_hash            TEXT UNIQUE,
  subtotal_cents        INTEGER,
  tax_cents             INTEGER,
  total_cents           INTEGER,
  computed_sum_cents    INTEGER,
  reconciliation_status TEXT NOT NULL DEFAULT 'ok',
  overall_confidence    REAL,
  status                TEXT NOT NULL DEFAULT 'draft',
  raw_model_json        TEXT,
  created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS receipt_items (
  id               INTEGER PRIMARY KEY,
  receipt_id       INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  raw_text         TEXT NOT NULL,
  name             TEXT NOT NULL,
  canonical_key    TEXT,
  qty              REAL NOT NULL DEFAULT 1,
  unit             TEXT NOT NULL DEFAULT 'each',
  unit_price_cents INTEGER NOT NULL DEFAULT 0,
  line_total_cents INTEGER NOT NULL DEFAULT 0,
  category         TEXT NOT NULL,
  is_perishable    INTEGER NOT NULL DEFAULT 0,
  confidence       REAL NOT NULL DEFAULT 1.0,
  needs_review     INTEGER NOT NULL DEFAULT 0,
  excluded         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pantry_items (
  id                    INTEGER PRIMARY KEY,
  receipt_item_id       INTEGER REFERENCES receipt_items(id),
  name                  TEXT NOT NULL,
  canonical_key         TEXT,
  category              TEXT NOT NULL,
  qty_initial           REAL NOT NULL,
  qty_remaining         REAL NOT NULL,
  unit                  TEXT NOT NULL,
  unit_price_cents      INTEGER NOT NULL,
  storage_method        TEXT NOT NULL,
  storage_temp_c        REAL,
  storage_duration_days INTEGER NOT NULL,
  shelf_life_source     TEXT NOT NULL,
  purchased_at          TEXT NOT NULL,
  best_by               TEXT NOT NULL,
  safe_until            TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'active',
  updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pantry_active ON pantry_items(status, best_by);

CREATE TABLE IF NOT EXISTS waste_events (
  id              INTEGER PRIMARY KEY,
  pantry_item_id  INTEGER NOT NULL REFERENCES pantry_items(id),
  portion         REAL NOT NULL,
  cost_lost_cents INTEGER NOT NULL,
  occurred_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meal_suggestions (
  id           INTEGER PRIMARY KEY,
  for_date     TEXT NOT NULL,
  pantry_hash  TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (for_date, pantry_hash)
);

CREATE TABLE IF NOT EXISTS insights_cache (
  id           INTEGER PRIMARY KEY,
  data_hash    TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shelf_life_reference (
  canonical_key      TEXT PRIMARY KEY,
  display_name       TEXT NOT NULL,
  category           TEXT NOT NULL,
  recommended_method TEXT NOT NULL,
  fridge_days        INTEGER,
  freezer_days       INTEGER,
  pantry_days        INTEGER,
  temp_c             REAL,
  eat_by_start_days  INTEGER NOT NULL DEFAULT 0,
  best_by_days       INTEGER,
  aliases            TEXT NOT NULL DEFAULT '[]',
  notes              TEXT
);

CREATE TABLE IF NOT EXISTS ai_call_usage (
  id         INTEGER PRIMARY KEY,
  operation  TEXT NOT NULL,
  called_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_call_usage_day ON ai_call_usage(called_at);
"""


def database_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else get_settings().database_path


@contextmanager
def connect(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    resolved_path = database_path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
    finally:
        connection.close()


def init_database(path: str | Path | None = None) -> Path:
    resolved_path = database_path(path)
    with connect(resolved_path) as connection:
        connection.executescript(SCHEMA)
    return resolved_path


def sync_shelf_life_reference(path: str | Path | None = None) -> int:
    from .services.shelf_life import load_reference_rows

    rows = load_reference_rows()
    init_database(path)
    with connect(path) as connection, connection:
        connection.executemany(
            """
            INSERT INTO shelf_life_reference (
              canonical_key, display_name, category, recommended_method,
              fridge_days, freezer_days, pantry_days, temp_c,
              eat_by_start_days, best_by_days, aliases, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
              display_name=excluded.display_name,
              category=excluded.category,
              recommended_method=excluded.recommended_method,
              fridge_days=excluded.fridge_days,
              freezer_days=excluded.freezer_days,
              pantry_days=excluded.pantry_days,
              temp_c=excluded.temp_c,
              eat_by_start_days=excluded.eat_by_start_days,
              best_by_days=excluded.best_by_days,
              aliases=excluded.aliases,
              notes=excluded.notes
            """,
            [
                (
                    row.canonical_key,
                    row.display_name,
                    row.category,
                    row.recommended_method,
                    row.fridge_days,
                    row.freezer_days,
                    row.pantry_days,
                    row.temp_c,
                    row.eat_by_start_days,
                    row.best_by_days,
                    json.dumps(row.aliases),
                    row.notes,
                )
                for row in rows
            ],
        )
    return len(rows)
