from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_demo_token
from ..models import InsightsResponse
from ..rate_limit import limiter, standard_minute_limit
from ..services.insights import get_insights


router = APIRouter(
    prefix="/v1/insights",
    tags=["insights"],
    dependencies=[Depends(require_demo_token)],
)


@router.get("", response_model=InsightsResponse)
@limiter.limit(standard_minute_limit)
async def insights(request: Request) -> InsightsResponse:
    return get_insights()
