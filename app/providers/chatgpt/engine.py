"""
OmniBridge – ChatGPT Provider Engine
Translates ChatGPT Web session protocol into OpenAI-compatible format.
Supports GPT-4o, GPT-4o-mini, o1-mini, and o3-mini via session_token or accessToken.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, Union

from curl_cffi.requests import AsyncSession

from app.models import UnifiedRequest
from app.providers.base import AbstractProvider, AuthError, ProviderError, RateLimitError

logger = logging.getLogger("omnibridge.chatgpt.engine")

_BASE_URL = "https://chatgpt.com"


class ChatGPTEngine(AbstractProvider):
    """
    Reverse proxy engine for ChatGPT web backend (chatgpt.com/backend-api/conversation).
    Uses curl-cffi for Chrome TLS fingerprint impersonation.
    """

    provider_name = "chatgpt"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.timeout = self.config.get("request_timeout", 90)
        self._access_token_cache: Dict[str, tuple[str, float]] = {}  # token_key -> (access_token, expiry_ts)

    def _extract_session_token(self, credentials: Dict[str, Any]) -> str:
        """Extract session token or access token from credentials."""
        if isinstance(credentials, str):
            return credentials.strip()
        token = (
            credentials.get("session_token")
            or credentials.get("sessionToken")
            or credentials.get("access_token")
            or credentials.get("accessToken")
            or credentials.get("token")
            or credentials.get("cookie")
            or ""
        )
        return str(token).strip()

    async def _get_access_token(self, session: AsyncSession, raw_token: str) -> str:
        """
        If raw_token is already a JWT access token (starts with eyJ), use directly.
        Otherwise, call /api/auth/session with __Secure-next-auth.session-token cookie.
        """
        # If it looks like a direct access token
        if raw_token.startswith("eyJ") and len(raw_token) > 200:
            return raw_token

        # Check cache
        if raw_token in self._access_token_cache:
            cached_token, exp = self._access_token_cache[raw_token]
            if time.time() < exp - 60:
                return cached_token

        # Fetch new access token using session cookie
        url = f"{_BASE_URL}/api/auth/session"
        cookies = {"__Secure-next-auth.session-token": raw_token}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": f"{_BASE_URL}/",
        }

        try:
            resp = await session.get(url, headers=headers, cookies=cookies, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                access_token = data.get("accessToken")
                if access_token:
                    # Cache for 10 minutes
                    self._access_token_cache[raw_token] = (access_token, time.time() + 600)
                    return str(access_token)
            logger.warning("ChatGPT /api/auth/session returned status %d", resp.status_code)
        except Exception as e:
            logger.warning("Failed to refresh ChatGPT access token: %s", e)

        # Fallback to using raw_token as Authorization Bearer
        return raw_token

    def _map_model(self, model_id: str) -> str:
        """Map OpenAI model ID to ChatGPT backend model name."""
        m = model_id.lower()
        if "4o-mini" in m:
            return "gpt-4o-mini"
        if "4o" in m:
            return "gpt-4o"
        if "o1-mini" in m:
            return "o1-mini"
        if "o3-mini" in m:
            return "o3-mini"
        if "o1" in m:
            return "o1"
        return "auto"

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
        Streaming chat yielding text chunks.
        """
        raw_token = self._extract_session_token(credentials)
        if not raw_token:
            raise AuthError("ChatGPT credentials missing session_token or access_token.")

        # Build prompt from unified messages
        if request.messages:
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

        backend_model = self._map_model(request.model_id)

        async with AsyncSession(impersonate="chrome120") as session:
            access_token = await self._get_access_token(session, raw_token)

            url = f"{_BASE_URL}/backend-api/conversation"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Origin": _BASE_URL,
                "Referer": f"{_BASE_URL}/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }

            payload = {
                "action": "next",
                "messages": [
                    {
                        "id": str(uuid.uuid4()),
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": [prompt]},
                        "metadata": {},
                    }
                ],
                "parent_message_id": str(uuid.uuid4()),
                "model": backend_model,
                "timezone_offset_min": -480,
                "history_and_training_disabled": False,
                "conversation_mode": {"kind": "primary_assistant"},
            }

            resp = await session.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.timeout,
            )

            if resp.status_code == 401:
                raise AuthError("ChatGPT session token is expired or invalid.")
            if resp.status_code == 429:
                raise RateLimitError("ChatGPT rate limit reached.")
            if resp.status_code != 200:
                raise ProviderError(
                    f"ChatGPT request failed (HTTP {resp.status_code})",
                    status_code=resp.status_code,
                    retry=resp.status_code >= 500,
                )

            # Cumulative buffer tracking
            last_text_len = 0

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

                message = chunk_json.get("message", {})
                content = message.get("content", {})
                parts = content.get("parts", [])

                if parts and isinstance(parts[0], str):
                    full_text = parts[0]
                    if len(full_text) > last_text_len:
                        delta = full_text[last_text_len:]
                        last_text_len = len(full_text)
                        yield delta

    async def validate(self, credentials: Dict[str, Any]) -> bool:
        """Validate ChatGPT credentials."""
        raw_token = self._extract_session_token(credentials)
        if not raw_token:
            return False

        try:
            async with AsyncSession(impersonate="chrome120") as session:
                access_token = await self._get_access_token(session, raw_token)
                url = f"{_BASE_URL}/backend-api/models"
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                }
                resp = await session.get(url, headers=headers, timeout=10)
                return resp.status_code == 200
        except Exception as e:
            logger.warning("ChatGPT validate failed: %s", e)
            return False
