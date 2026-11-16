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


def _normalize_single_tool_call(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    call_id = item.get("id") or f"call_{uuid.uuid4().hex[:8]}"

    # Format A: {"function": {"name": ..., "arguments": ...}}
    if "function" in item and isinstance(item["function"], dict):
        fn = item["function"]
        name = fn.get("name", "")
        args = fn.get("arguments", "{}")
    # Format B: {"name": ..., "arguments": ...} or {"name": ..., "parameters": ...}
    elif "name" in item:
        name = item.get("name", "")
        args = item.get("arguments") or item.get("parameters") or "{}"
    else:
        return None

    if not name or not isinstance(name, str):
        return None

    if isinstance(args, (dict, list)):
        args = json.dumps(args, ensure_ascii=False)
    elif not isinstance(args, str):
        args = "{}"

    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name.strip(),
            "arguments": args
        }
    }


def _extract_json_objects(text: str) -> list:
    """Extract all valid JSON objects/arrays from text using balanced bracket matching."""
    candidates = []
    # 1. First try markdown blocks ```(?:json)? ... ```
    for m in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', text):
        block = m.group(1).strip()
        try:
            parsed = json.loads(block)
            candidates.append(parsed)
        except Exception:
            pass

    # 2. Balanced bracket scan
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        i = 0
        while i < len(text):
            if text[i] == start_char:
                depth = 0
                in_string = False
                escape = False
                start_pos = i
                for j in range(i, len(text)):
                    c = text[j]
                    if escape:
                        escape = False
                        continue
                    if c == '\\':
                        escape = True
                        continue
                    if c == '"':
                        in_string = not in_string
                        continue
                    if not in_string:
                        if c == start_char:
                            depth += 1
                        elif c == end_char:
                            depth -= 1
                            if depth == 0:
                                substr = text[start_pos:j+1].strip()
                                try:
                                    parsed = json.loads(substr)
                                    if parsed not in candidates:
                                        candidates.append(parsed)
                                except Exception:
                                    pass
                                i = j
                                break
                i += 1
            else:
                i += 1
    return candidates


def _parse_emulated_tool_call(text: str) -> list | None:
    if not text:
        return None

    candidates = _extract_json_objects(text)
    for data in candidates:
        # Case 1: {"tool_calls": [...]}
        if isinstance(data, dict) and "tool_calls" in data and isinstance(data["tool_calls"], list):
            norm_calls = []
            for tc in data["tool_calls"]:
                norm = _normalize_single_tool_call(tc)
                if norm:
                    norm_calls.append(norm)
            if norm_calls:
                return norm_calls

        # Case 2: List of tool calls: [{"name": ..., "arguments": ...}] or [{"function": ...}]
        elif isinstance(data, list) and data:
            norm_calls = []
            for item in data:
                norm = _normalize_single_tool_call(item)
                if norm:
                    norm_calls.append(norm)
            if norm_calls:
                return norm_calls

        # Case 3: Single tool call: {"name": ..., "arguments": ...}
        elif isinstance(data, dict) and ("name" in data or "function" in data):
            norm = _normalize_single_tool_call(data)
            if norm:
                return [norm]

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
    yield f"data: {first_chunk.model_dump_json(exclude_none=True)}\n\n"

    success = True
    
    # If tools are present, buffer stream to intercept tool calls
    if unified.tools:
        buffer = ""
        try:
            async for chunk_item in pool.stream_chat(unified):
                if isinstance(chunk_item, dict) and "reasoning_content" in chunk_item:
                    # Stream reasoning content immediately
                    r_chunk = ChatCompletionChunk(
                        id=request_id,
                        model=model_id,
                        choices=[StreamChoice(index=0, delta=DeltaMessage(reasoning_content=chunk_item["reasoning_content"]))],
                    )
                    yield f"data: {r_chunk.model_dump_json(exclude_none=True)}\n\n"
                elif isinstance(chunk_item, str):
                    buffer += chunk_item
                elif isinstance(chunk_item, dict) and "content" in chunk_item:
                    buffer += chunk_item["content"]
            
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
                
                latency_ms = (time.monotonic() - start_ts) * 1000
                await record_metric(provider_name, model_id, True, latency_ms)
                return
            else:
                # Not a tool call, yield buffered text
                chunk = ChatCompletionChunk(
                    id=request_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(content=buffer))]
                )
                yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
        
        except (RateLimitError, AuthError, ProviderError, Exception) as e:
            error_chunk = {"error": {"message": str(e), "type": "provider_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            latency_ms = (time.monotonic() - start_ts) * 1000
            await record_metric(provider_name, model_id, False, latency_ms)
            return
            
    else:
        # Standard streaming
        try:
            async for chunk_item in pool.stream_chat(unified):
                if isinstance(chunk_item, dict) and "reasoning_content" in chunk_item:
                    chunk = ChatCompletionChunk(
                        id=request_id,
                        model=model_id,
                        choices=[StreamChoice(index=0, delta=DeltaMessage(reasoning_content=chunk_item["reasoning_content"]))]
                    )
                elif isinstance(chunk_item, dict) and "content" in chunk_item:
                    chunk = ChatCompletionChunk(
                        id=request_id,
                        model=model_id,
                        choices=[StreamChoice(index=0, delta=DeltaMessage(content=chunk_item["content"]))]
                    )
                else:
                    chunk = ChatCompletionChunk(
                        id=request_id,
                        model=model_id,
                        choices=[StreamChoice(index=0, delta=DeltaMessage(content=str(chunk_item)))]
                    )
                yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
        except (RateLimitError, AuthError, ProviderError, Exception) as e:
            error_chunk = {"error": {"message": str(e), "type": "provider_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            latency_ms = (time.monotonic() - start_ts) * 1000
            await record_metric(provider_name, model_id, False, latency_ms)
            return

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
    await record_metric(provider_name, model_id, True, latency_ms)
