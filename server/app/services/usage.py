from __future__ import annotations

from ..config import get_settings
from ..db import connect, init_database
from ..errors import AppError


def reserve_ai_call(operation: str) -> int:
    settings = get_settings()
    limit = settings.openai_daily_call_limit
    if limit <= 0:
        raise AppError(
            503,
            "BUDGET_PAUSE",
            "OpenAI calls are disabled by OPENAI_DAILY_CALL_LIMIT.",
            "FreshLedger's demo budget is resting — try a saved receipt instead.",
        )

    init_database()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        used = int(
            connection.execute(
                "SELECT COUNT(*) FROM ai_call_usage "
                "WHERE called_at >= datetime('now', 'start of day')"
            ).fetchone()[0]
        )
        if used >= limit:
            connection.rollback()
            raise AppError(
                503,
                "BUDGET_PAUSE",
                f"The daily OpenAI call limit of {limit} has been reached.",
                "FreshLedger's demo budget is resting — try a saved receipt instead.",
            )
        connection.execute(
            "INSERT INTO ai_call_usage (operation) VALUES (?)", (operation,)
        )
        connection.commit()
    return used + 1
