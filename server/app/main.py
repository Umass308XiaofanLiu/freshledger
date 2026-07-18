from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.errors import RateLimitExceeded

from .config import get_settings
from .db import init_database, sync_shelf_life_reference
from .errors import AppError
from .rate_limit import limiter
from .routers.demo import router as demo_router
from .routers.insights import router as insights_router
from .routers.meals import router as meals_router
from .routers.pantry import router as pantry_router
from .routers.receipts import router as receipts_router
from .services.shelf_life import load_reference_rows


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    load_reference_rows()
    sync_shelf_life_reference()
    if get_settings().auto_seed_demo_data:
        from .services.demo_data import database_is_empty, seed_demo_data

        try:
            if database_is_empty():
                seed_demo_data()
        except Exception:
            logger.exception("Automatic demo seed failed; API will start with empty data")
    yield


app = FastAPI(title="FreshLedger API", version="0.2.0", lifespan=lifespan)
app.state.limiter = limiter
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


@app.exception_handler(RateLimitExceeded)
async def rate_limit_error_handler(
    _request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    error = AppError(
        429,
        "RATE_LIMITED",
        str(exc.detail),
        "Whoa, the fridge needs a breather — try again in a minute.",
    )
    return JSONResponse(status_code=error.status_code, content=error.payload())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    error = AppError(
        422,
        "VALIDATION_ERROR",
        f"The request did not match the API contract: {exc.errors()}",
        "Some receipt details were invalid — check them and try again.",
    )
    return JSONResponse(status_code=error.status_code, content=error.payload())


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404:
        code = "NOT_FOUND"
        user_message = "That FreshLedger page or sample could not be found."
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
        user_message = "That action is not available here."
    else:
        code = "HTTP_ERROR"
        user_message = "FreshLedger could not complete that request."
    error = AppError(exc.status_code, code, str(exc.detail), user_message)
    return JSONResponse(status_code=error.status_code, content=error.payload())


@app.exception_handler(Exception)
async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled FreshLedger API error", exc_info=exc)
    error = AppError(
        500,
        "INTERNAL_ERROR",
        "An unexpected server error occurred.",
        "FreshLedger hit a snag — please try again.",
    )
    return JSONResponse(status_code=error.status_code, content=error.payload())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "freshledger"}


app.include_router(receipts_router)
app.include_router(demo_router)
app.include_router(pantry_router)
app.include_router(meals_router)
app.include_router(insights_router)
