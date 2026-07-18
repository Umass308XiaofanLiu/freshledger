from __future__ import annotations

import secrets

from fastapi import Header, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .errors import AppError


bearer_scheme = HTTPBearer(auto_error=False)


async def require_demo_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    expected = get_settings().demo_token
    if not expected:
        raise AppError(
            503,
            "SERVER_NOT_CONFIGURED",
            "DEMO_TOKEN is not configured.",
            "FreshLedger is still warming up — please try again shortly.",
        )
    provided = credentials.credentials if credentials else ""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(provided, expected)
    ):
        raise AppError(
            401,
            "UNAUTHORIZED",
            "A valid demo bearer token is required.",
            "This demo link is missing its access token.",
        )


async def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise AppError(
            401,
            "ADMIN_UNAUTHORIZED",
            "A valid X-Admin-Token is required.",
            "This reset action needs the local administrator token.",
        )
