"""
OmniBridge – Pydantic Models
OpenAI-compatible and Anthropic-compatible request/response schemas.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible schemas
# ─────────────────────────────────────────────────────────────────────────────

class ImageUrl(BaseModel):
    url: str
    detail: Optional[str] = "auto"


class ContentPartText(BaseModel):
    type: Literal["text"]
    text: str


class ContentPartImage(BaseModel):
    type: Literal["image_url"]
    image_url: ImageUrl


ContentPart = Union[ContentPartText, ContentPartImage]


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "function"]
    content: Union[str, List[ContentPart], None] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def get_text(self) -> str:
        """Extract plain text regardless of content format."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = []
            for part in self.content:
                if isinstance(part, ContentPartText):
                    parts.append(part.text)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            return "\n".join(parts)
        return ""

    def get_images(self) -> List[str]:
        """Extract base64 or URL images."""
        if isinstance(self.content, list):
            images = []
            for part in self.content:
                if isinstance(part, ContentPartImage):
                    images.append(part.image_url.url)
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    images.append(part["image_url"]["url"])
            return images
        return []


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    n: Optional[int] = 1
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    user: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageStats = Field(default_factory=UsageStats)


# ── Streaming delta ───────────────────────────────────────────────────────────

class DeltaFunctionCall(BaseModel):
    name: Optional[str] = None
    arguments: Optional[str] = None


class DeltaToolCall(BaseModel):
    index: int
    id: Optional[str] = None
    type: str = "function"
    function: DeltaFunctionCall


class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[DeltaToolCall]] = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[StreamChoice]


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic-compatible schemas
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicContentBlock(BaseModel):
    type: str  # "text" | "image" | "tool_use" | "tool_result"
    text: Optional[str] = None
    source: Optional[Dict[str, Any]] = None


class AnthropicMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Union[str, List[AnthropicContentBlock]]

    def get_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        texts = [b.text for b in self.content if b.type == "text" and b.text]
        return "\n".join(texts)

    def get_images(self) -> List[str]:
        if isinstance(self.content, str):
            return []
        images = []
        for b in self.content:
            if b.type == "image" and b.source:
                # format for anthropic is data base64
                media_type = b.source.get("media_type", "image/jpeg")
                data = b.source.get("data", "")
                if data:
                    images.append(f"data:{media_type};base64,{data}")
        return images


class AnthropicRequest(BaseModel):
    model: str
    messages: List[AnthropicMessage]
    system: Optional[str] = None
    max_tokens: int = 4096
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None


class AnthropicUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicResponseContent(BaseModel):
    type: str = "text"
    text: str


class AnthropicResponse(BaseModel):
    id: str
    type: str = "message"
    role: str = "assistant"
    model: str
    content: List[AnthropicResponseContent]
    stop_reason: Optional[str] = "end_turn"
    usage: AnthropicUsage = Field(default_factory=AnthropicUsage)


# ─────────────────────────────────────────────────────────────────────────────
# Internal unified format (used between routes and provider engines)
# ─────────────────────────────────────────────────────────────────────────────

class UnifiedMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    text: str
    images: List[str] = Field(default_factory=list)  # base64 data URIs or https URLs


class UnifiedRequest(BaseModel):
    model_id: str
    provider: str
    messages: List[UnifiedMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    tools: Optional[List[Dict[str, Any]]] = None


def openai_request_to_unified(req: ChatCompletionRequest, provider: str) -> UnifiedRequest:
    msgs = []
    for m in req.messages:
        if m.role == "tool" and m.tool_call_id:
            msgs.append(UnifiedMessage(
                role="user",
                text=f"--- TOOL RESULT FOR {m.name or m.tool_call_id} ---\n{m.get_text()}\n----------------------\n\nAnalyze the tool result and continue following your main system instructions."
            ))
        elif m.role == "assistant" and m.tool_calls:
            calls_list = []
            for tc in m.tool_calls:
                calls_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                })
            calls_text = "```json\n" + json.dumps({"tool_calls": calls_list}, ensure_ascii=False, indent=2) + "\n```"
            text_part = m.get_text()
            full_text = f"{text_part}\n{calls_text}" if text_part else calls_text
            msgs.append(UnifiedMessage(
                role="assistant",
                text=full_text
            ))
        else:
            msgs.append(UnifiedMessage(
                role=m.role,
                text=m.get_text(),
                images=m.get_images(),
            ))

    if req.tools:
        tools_str = json.dumps([t for t in req.tools], ensure_ascii=False, indent=2)
        tool_prompt = (
            "You are an expert AI agent. You have access to tools.\n"
            "Whenever you decide to use a tool (including tools to talk to the user or finish tasks), you MUST output a JSON block in exactly the following format. "
            "Do NOT output any conversational text outside the JSON block.\n"
            "```json\n"
            "{\n"
            '  "tool_calls": [\n'
            '    {\n'
            '      "id": "call_abc123",\n'
            '      "type": "function",\n'
            '      "function": {\n'
            '        "name": "tool_name",\n'
            '        "arguments": "{\\"arg1\\": \\"value1\\"}"\n'
            '      }\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n\n"
        )
        # Prepend to the first system message, or insert as first message
        if msgs and msgs[0].role == "system":
            msgs[0].text = f"{tool_prompt}\n\n{msgs[0].text}"
        else:
            msgs.insert(0, UnifiedMessage(role="system", text=tool_prompt))
    
    return UnifiedRequest(
        model_id=req.model,
        provider=provider,
        messages=msgs,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=req.stream,
        tools=req.tools,
    )


def anthropic_request_to_unified(req: AnthropicRequest, provider: str) -> UnifiedRequest:
    msgs = []
    if req.system:
        msgs.append(UnifiedMessage(role="system", text=req.system))
    for m in req.messages:
        msgs.append(UnifiedMessage(role=m.role, text=m.get_text(), images=m.get_images()))
    return UnifiedRequest(
        model_id=req.model,
        provider=provider,
        messages=msgs,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=req.stream,
    )
