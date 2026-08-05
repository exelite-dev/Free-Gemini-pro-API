"""
OmniBridge – OpenAI-Compatible Routes
  GET  /v1/models
  POST /v1/chat/completions  (streaming + non-streaming)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth import require_api_key
from app.config import settings
from app.database import get_all_provider_states, record_metric
from app.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    DeltaMessage,
    StreamChoice,
    UsageStats,
    openai_request_to_unified,
    ToolCall,
    DeltaToolCall,
    FunctionCall,
    DeltaFunctionCall,
)
import re
from app.providers.base import AuthError, ProviderError, RateLimitError
from app.providers.registry import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()

async def _get_provider_for_model(model_id: str) -> str:
    all_models = settings.get_all_models()
    model_def = next((m for m in all_models if m.id == model_id), None)
    if not model_def:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")

    provider_states = await get_all_provider_states()
    if not provider_states.get(model_def.provider, True):
        raise HTTPException(
            status_code=503,
            detail=f"Provider '{model_def.provider}' is currently disabled.",
        )
    return model_def.provider


def _parse_emulated_tool_call(text: str) -> list | None:
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    json_str = match.group(1) if match else text.strip()
    try:
        data = json.loads(json_str)
        if isinstance(data, dict) and "tool_calls" in data:
            return data["tool_calls"]
    except Exception:
        pass
    return None

# ── GET /v1/models ─────────────────────────────────────────────────────────────

@router.get("/v1/models")
async def list_models(_key: str = Depends(require_api_key)):
    models = settings.get_all_models()
    return {
        "object": "list",
        "data": [m.to_openai_dict() for m in models],
    }


# ── POST /v1/chat/completions ──────────────────────────────────────────────────

@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    _key: str = Depends(require_api_key),
):
    provider_name = await _get_provider_for_model(req.model)
    pool = get_pool(provider_name)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=f"Provider '{provider_name}' engine not available.",
        )

    unified = openai_request_to_unified(req, provider_name)
    request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    start_ts = time.monotonic()

    if req.stream:
        return StreamingResponse(
            _openai_sse_generator(pool, unified, req.model, request_id, start_ts, provider_name),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
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
    except Exception as e:
        logger.exception("Unhandled error in chat_completions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Check for emulated tool calls
    tool_calls = None
    finish_reason = "stop"
    if req.tools:
        parsed_tools = _parse_emulated_tool_call(text)
        if parsed_tools:
            tool_calls = []
            for tc in parsed_tools:
                args = tc.get("function", {}).get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                tool_calls.append(ToolCall(
                    id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    type="function",
                    function=FunctionCall(
                        name=tc.get("function", {}).get("name", ""),
                        arguments=args
                    )
                ))
            finish_reason = "tool_calls"
            text = None

    response = ChatCompletionResponse(
        id=request_id,
        model=req.model,
        choices=[
            {
                "index": 0,
                "message": ChatMessage(role="assistant", content=text, tool_calls=tool_calls),
                "finish_reason": finish_reason,
            }
        ],
        usage=UsageStats(
            prompt_tokens=sum(len(str(m.content or "")) for m in req.messages) // 4,
            completion_tokens=(len(text) // 4) if text else 0,
            total_tokens=(sum(len(str(m.content or "")) for m in req.messages) + (len(text) if text else 0)) // 4,
        ),
    )
    return response


async def _openai_sse_generator(
    pool,
    unified,
    model_id: str,
    request_id: str,
    start_ts: float,
    provider_name: str,
) -> AsyncGenerator[str, None]:
    """Yield OpenAI-format SSE chunks."""
    created = int(time.time())
    # Send role chunk first
    first_chunk = ChatCompletionChunk(
        id=request_id,
        model=model_id,
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(role="assistant", content=""),
                finish_reason=None,
            )
        ],
    )
    yield f"data: {first_chunk.model_dump_json()}\n\n"

    success = True
    
    # If tools are present, buffer stream to intercept tool calls
    if unified.tools:
        buffer = ""
        try:
            logger.info(f"--- UNIFIED MESSAGES TO GEMINI ---")
            for m in unified.messages:
                logger.info(f"ROLE: {m.role} | CONTENT: {m.text[:300]}...")
            
            async for text_chunk in pool.stream_chat(unified):
                buffer += text_chunk
                
            logger.info(f"--- GEMINI RAW BUFFER ---")
            logger.info(buffer)
            
            # Post-process buffer
            parsed_tools = _parse_emulated_tool_call(buffer)
            if parsed_tools:
                delta_tool_calls = []
                for i, tc in enumerate(parsed_tools):
                    args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    delta_tool_calls.append(DeltaToolCall(
                        index=i,
                        id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        type="function",
                        function=DeltaFunctionCall(
                            name=tc.get("function", {}).get("name", ""),
                            arguments=args
                        )
                    ))
                
                chunk = ChatCompletionChunk(
                    id=request_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(tool_calls=delta_tool_calls))]
                )
                yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
                
                stop_chunk = ChatCompletionChunk(
                    id=request_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(), finish_reason="tool_calls")]
                )
                yield f"data: {stop_chunk.model_dump_json(exclude_none=True)}\n\n"
                yield "data: [DONE]\n\n"
                return
            else:
                # Not a tool call, yield buffered text
                chunk = ChatCompletionChunk(
                    id=request_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(content=buffer))]
                )
                yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
        
        except (RateLimitError, AuthError, ProviderError) as e:
            success = False
            error_chunk = {"error": {"message": str(e), "type": "provider_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            
    else:
        # Standard streaming
        try:
            async for text_chunk in pool.stream_chat(unified):
                chunk = ChatCompletionChunk(
                    id=request_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(content=text_chunk))]
                )
                yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
        except (RateLimitError, AuthError, ProviderError) as e:
            success = False
            error_chunk = {"error": {"message": str(e), "type": "provider_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"

    # Final stop chunk
    stop_chunk = ChatCompletionChunk(
        id=request_id,
        model=model_id,
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {stop_chunk.model_dump_json(exclude_none=True)}\n\n"
    yield "data: [DONE]\n\n"

    latency_ms = (time.monotonic() - start_ts) * 1000
    await record_metric(provider_name, model_id, success, latency_ms)
