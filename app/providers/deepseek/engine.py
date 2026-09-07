"""
OmniBridge – DeepSeek Provider Engine
Translates DeepSeek web session protocol into OpenAI-compatible format.
Supports DeepSeek-V3 (deepseek-chat) and DeepSeek-R1 (deepseek-reasoner)
with automatic PoW challenge solving and reasoning_content (thinking) stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, Union

from curl_cffi.requests import AsyncSession

from app.models import UnifiedRequest
from app.providers.base import AbstractProvider, AuthError, ProviderError, RateLimitError
from app.providers.deepseek.pow_solver import get_and_solve_pow

logger = logging.getLogger("omnibridge.deepseek.engine")

_BASE_URL = "https://chat.deepseek.com"


class DeepSeekEngine(AbstractProvider):
    """
    Reverse proxy engine for DeepSeek web API (chat.deepseek.com).
    Uses curl-cffi for Chrome TLS fingerprint impersonation and automatic PoW solving.
    """

    provider_name = "deepseek"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.timeout = self.config.get("request_timeout", 90)

    def _extract_token(self, credentials: Dict[str, Any]) -> str:
        """Extract userToken from credentials dictionary or string."""
        if isinstance(credentials, str):
            return credentials.strip()
        token = (
            credentials.get("userToken")
            or credentials.get("token")
            or credentials.get("user_token")
            or credentials.get("cookie")
            or ""
        )
        return str(token).strip()

    def _build_headers(self, token: str, pow_header: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Origin": _BASE_URL,
            "Referer": f"{_BASE_URL}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "x-app-version": "20241129.1",
            "x-client-version": "1.0.0-always",
            "x-client-platform": "web",
            "x-client-locale": "zh_CN",
        }
        if pow_header:
            headers["x-ds-pow-response"] = pow_header
        return headers

    async def _create_chat_session(self, session: AsyncSession, token: str) -> str:
        """Create a new chat session on DeepSeek web backend."""
        url = f"{_BASE_URL}/api/v0/chat_session/create"
        headers = self._build_headers(token)
        payload = {"character_id": None}

        resp = await session.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 401:
            raise AuthError("DeepSeek userToken is invalid or expired.")
        if resp.status_code == 429:
            raise RateLimitError("DeepSeek session rate limit reached.")
        if resp.status_code != 200:
            raise ProviderError(
                f"DeepSeek create session failed (HTTP {resp.status_code}): {resp.text[:200]}",
                status_code=resp.status_code,
            )

        data = resp.json()
        session_id = data.get("data", {}).get("biz_data", {}).get("id")
        if not session_id:
            raise ProviderError("Failed to parse DeepSeek chat_session_id.")
        return str(session_id)

    async def chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> str:
        """Non-streaming chat: accumulates response text and returns full string."""
        text_parts = []
        async for chunk in self.stream_chat(request, account_id, credentials):
            if isinstance(chunk, str):
                text_parts.append(chunk)
            elif isinstance(chunk, dict) and "content" in chunk:
                text_parts.append(chunk["content"])
        return "".join(text_parts)

    async def stream_chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        Streaming chat yielding content chunks or reasoning_content deltas:
        - Plain text delta: yields str
        - Reasoning delta: yields {"reasoning_content": str}
        """
        token = self._extract_token(credentials)
        if not token:
            raise AuthError("DeepSeek credentials missing userToken.")

        # Determine thinking mode
        is_reasoner = "reasoner" in request.model_id.lower() or "r1" in request.model_id.lower()

        # Build prompt from unified messages
        prompt = ""
        if request.messages:
            # Build conversation context
            history = []
            for m in request.messages[:-1]:
                history.append(f"{m.role.capitalize()}: {m.text}")
            last_msg = request.messages[-1].text
            if history:
                prompt = "\n\n".join(history) + f"\n\nUser: {last_msg}"
            else:
                prompt = last_msg
        else:
            prompt = "Hello"

        async with AsyncSession(impersonate="chrome120") as session:
            # 1. Create chat session
            chat_session_id = await self._create_chat_session(session, token)

            # 2. Get and solve PoW challenge
            pow_header = await get_and_solve_pow(
                session, token, target_path="/api/v0/chat/completion"
            )

            # 3. Send chat completion request
            url = f"{_BASE_URL}/api/v0/chat/completion"
            headers = self._build_headers(token, pow_header=pow_header)
            payload = {
                "chat_session_id": chat_session_id,
                "parent_message_id": None,
                "prompt": prompt,
                "ref_file_ids": [],
                "thinking_enabled": is_reasoner,
                "search_enabled": False,
            }

            resp = await session.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.timeout,
            )

            if resp.status_code == 401:
                raise AuthError("DeepSeek token expired or invalid (HTTP 401).")
            if resp.status_code == 429:
                raise RateLimitError("DeepSeek rate limited (HTTP 429).")
            if resp.status_code != 200:
                raise ProviderError(
                    f"DeepSeek chat failed (HTTP {resp.status_code})",
                    status_code=resp.status_code,
                    retry=resp.status_code >= 500,
                )

            # 4. Parse SSE stream
            async for line_bytes in resp.aiter_lines():
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8") if isinstance(line_bytes, bytes) else str(line_bytes)
                line = line.strip()

                if not line.startswith("data:"):
                    continue

                raw_data = line[5:].strip()
                if not raw_data or raw_data == "[DONE]":
                    continue

                try:
                    chunk_json = json.loads(raw_data)
                except Exception:
                    continue

                biz_data = chunk_json.get("data", {}).get("biz_data", {})
                if not biz_data:
                    # Alternative structure
                    biz_data = chunk_json.get("choices", [{}])[0].get("delta", {})

                # Check for thinking (reasoning) content
                thinking_chunk = (
                    biz_data.get("thinking_content")
                    or biz_data.get("reasoning_content")
                    or biz_data.get("thinking")
                )
                if thinking_chunk:
                    yield {"reasoning_content": thinking_chunk}

                # Check for standard response text content
                content_chunk = (
                    biz_data.get("content")
                    or biz_data.get("text")
                    or biz_data.get("fragment")
                )
                if content_chunk:
                    yield content_chunk

    async def validate(self, credentials: Dict[str, Any]) -> bool:
        """Validate token by testing session creation."""
        token = self._extract_token(credentials)
        if not token:
            return False

        try:
            async with AsyncSession(impersonate="chrome120") as session:
                url = f"{_BASE_URL}/api/v0/users/current"
                headers = self._build_headers(token)
                resp = await session.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        return True
                # Fallback to test chat session creation
                sess_id = await self._create_chat_session(session, token)
                return bool(sess_id)
        except Exception as e:
            logger.warning("DeepSeek validate failed: %s", e)
            return False
