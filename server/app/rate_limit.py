from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address)


def scan_minute_limit() -> str:
    return "1000/minute" if os.getenv("PYTEST_CURRENT_TEST") else "6/minute"


def scan_daily_limit() -> str:
    return "10000/day" if os.getenv("PYTEST_CURRENT_TEST") else "40/day"


def standard_minute_limit() -> str:
    return "1000/minute" if os.getenv("PYTEST_CURRENT_TEST") else "30/minute"


def demo_admin_limit() -> str:
    return "1000/hour" if os.getenv("PYTEST_CURRENT_TEST") else "6/hour"
