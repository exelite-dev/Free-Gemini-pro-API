"""
OmniBridge – Anthropic-Compatible Routes
  POST /anthropic/v1/messages  (streaming + non-streaming)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import require_api_key
from app.database import get_all_provider_states, record_metric
from app.models import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicResponseContent,
    AnthropicUsage,
    anthropic_request_to_unified,
)
from app.providers.base import AuthError, ProviderError, RateLimitError
from app.providers.registry import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()

async def _get_provider(model_id: str) -> str:
    from app.config import settings
    all_models = settings.get_all_models()
    model_def = next((m for m in all_models if m.id == model_id), None)
    if not model_def:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")

    states = await get_all_provider_states()
    if not states.get(model_def.provider, True):
        raise HTTPException(status_code=503, detail=f"Provider '{model_def.provider}' is disabled.")
    return model_def.provider


@router.post("/anthropic/v1/messages")
async def anthropic_messages(
    req: AnthropicRequest,
    _key: str = Depends(require_api_key),
):
    provider_name = await _get_provider(req.model)
    pool = get_pool(provider_name)
    if not pool:
        raise HTTPException(status_code=503, detail=f"Provider '{provider_name}' not available.")

    unified = anthropic_request_to_unified(req, provider_name)
    request_id = f"msg_{uuid.uuid4().hex[:24]}"
    start_ts = time.monotonic()

    if req.stream:
        return StreamingResponse(
            _anthropic_sse_generator(pool, unified, req.model, request_id, start_ts, provider_name),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming
    try:
        text = await pool.chat(unified)
        latency_ms = (time.monotonic() - start_ts) * 1000
        await record_metric(provider_name, req.model, True, latency_ms)
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except ProviderError as e:
        await record_metric(provider_name, req.model, False, 0)
        raise HTTPException(status_code=e.status_code, detail=str(e))

    prompt_tokens = sum(len(m.get_text()) for m in req.messages) // 4
    completion_tokens = len(text) // 4

    return AnthropicResponse(
        id=request_id,
        model=req.model,
        content=[AnthropicResponseContent(type="text", text=text)],
        usage=AnthropicUsage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        ),
    )


async def _anthropic_sse_generator(
    pool, unified, model_id, request_id, start_ts, provider_name
) -> AsyncGenerator[str, None]:
    """Yield Anthropic-format SSE events."""

    def _event(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    yield _event("message_start", {
        "type": "message_start",
        "message": {
            "id": request_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_id,
            "stop_reason": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _event("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    })

    success = True
    output_chars = 0
    try:
        async for chunk in pool.stream_chat(unified):
            output_chars += len(chunk)
            yield _event("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            })
    except (RateLimitError, AuthError, ProviderError) as e:
        success = False
        yield _event("error", {"type": "error", "error": {"type": "provider_error", "message": str(e)}})

    yield _event("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_chars // 4},
    })
    yield _event("message_stop", {"type": "message_stop"})

    latency_ms = (time.monotonic() - start_ts) * 1000
    await record_metric(provider_name, model_id, success, latency_ms)
