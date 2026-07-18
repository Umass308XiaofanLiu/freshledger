from __future__ import annotations

import base64
from functools import lru_cache

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from ..config import get_settings
from ..errors import AppError
from ..models import ReceiptParse
from ..prompts import RECEIPT_SYSTEM_PROMPT
from .usage import reserve_ai_call


@lru_cache(maxsize=2)
def _openai_client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, timeout=60.0, max_retries=1)


async def parse_receipt_image(jpeg_bytes: bytes) -> ReceiptParse:
    settings = get_settings()
    if not settings.openai_api_key:
        raise AppError(
            503,
            "OPENAI_NOT_CONFIGURED",
            "OPENAI_API_KEY is not configured.",
            "The receipt reader is not configured yet — please try again shortly.",
        )

    reserve_ai_call("receipt_scan")

    image_url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
    client = _openai_client(settings.openai_api_key)

    try:
        response = await client.responses.parse(
            model=settings.openai_model,
            instructions=RECEIPT_SYSTEM_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Parse every visible line item in this grocery receipt photo.",
                        },
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=ReceiptParse,
            reasoning={"effort": "low"},
            max_output_tokens=4096,
            store=False,
        )
    except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
        raise AppError(
            504,
            "AI_TIMEOUT",
            "The OpenAI receipt parse did not complete in time.",
            "The AI kitchen is busy — try again in a few seconds.",
        ) from exc
    except APIStatusError as exc:
        if exc.status_code >= 500:
            raise AppError(
                504,
                "AI_TIMEOUT",
                f"The OpenAI API returned transient status {exc.status_code}.",
                "The AI kitchen is busy — try again in a few seconds.",
            ) from exc
        raise AppError(
            502,
            "AI_ERROR",
            f"The OpenAI API returned status {exc.status_code}.",
            "The receipt reader hiccuped — please try scanning again.",
        ) from exc
    except ValidationError as exc:
        raise AppError(
            502,
            "PARSE_FAILED",
            f"The structured receipt response failed validation: {exc}",
            "The receipt reader hiccuped — please try scanning again.",
        ) from exc

    parsed = response.output_parsed
    if parsed is None:
        raise AppError(
            502,
            "PARSE_FAILED",
            "The structured receipt response was empty or refused.",
            "The receipt reader hiccuped — please try scanning again.",
        )
    return parsed
