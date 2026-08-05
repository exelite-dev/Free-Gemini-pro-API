"""
OmniBridge – Gemini Engine
Cookie-based auth via __Secure-1PSID + __Secure-1PSIDTS.
Uses curl-cffi for TLS fingerprint impersonation (Chrome 120).
Request format reverse-engineered from gemini-webapi v2.0.0.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from curl_cffi.requests import AsyncSession

from app.models import UnifiedMessage, UnifiedRequest
from app.providers.base import AbstractProvider, AuthError, ProviderError, RateLimitError

logger = logging.getLogger(__name__)

# ── Gemini web API endpoints ──────────────────────────────────────────────────
_GENERATE_URL = (
    "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/"
    "StreamGenerate"
)
_INIT_URL = "https://gemini.google.com/app"

# ── Model IDs → internal Gemini model header IDs ──────────────────────────────
_MODEL_HEADERS: Dict[str, Dict[str, str]] = {
    "gemini-3.5-flash-lite": {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-3-5-flash-lite",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-3.6-flash":      {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-3-6-flash",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-3.1-pro":        {"x-goog-ext-525001261-jspb": '[1,null,null,null,"9d8ca3786ebdfbea",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-pro-extended":   {"x-goog-ext-525001261-jspb": '[1,null,null,null,"9d8ca3786ebdfbea",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[1]", "x-goog-ext-73010990-jspb": "[1]"},
    "gemini-3-pro":          {"x-goog-ext-525001261-jspb": '[1,null,null,null,"9d8ca3786ebdfbea",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-3-flash":        {"x-goog-ext-525001261-jspb": '[1,null,null,null,"fbb127bbb056c959",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-2.5-pro":        {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-2-5-pro",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-2.5-flash":      {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-2-5-flash",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-1.5-pro":        {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-1-5-pro",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
    "gemini-1.5-flash":      {"x-goog-ext-525001261-jspb": '[1,null,null,null,"gemini-1-5-flash",null,null,0,[4],null,null,1]', "x-goog-ext-73010989-jspb": "[0]", "x-goog-ext-73010990-jspb": "[0]"},
}

_MODEL_ALIASES: Dict[str, str] = {
    "3.5-flash-lite": "gemini-3.5-flash-lite",
    "flash-lite": "gemini-3.5-flash-lite",
    "3.6-flash": "gemini-3.6-flash",
    "3.1-pro": "gemini-3.1-pro",
    "pro-extended": "gemini-pro-extended",
    "extended-thinking": "gemini-pro-extended",
}
_DEFAULT_MODEL_HEADERS = _MODEL_HEADERS["gemini-3.6-flash"]

_DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]
_STREAMING_FLAG_INDEX = 7


def _get_model_headers(model_id: str) -> Dict[str, str]:
    target_id = _MODEL_ALIASES.get(model_id, model_id)
    return _MODEL_HEADERS.get(target_id, _DEFAULT_MODEL_HEADERS)


def _build_cookie_header(credentials: Dict[str, Any]) -> str:
    psid = (
        credentials.get("secure_1psid")
        or credentials.get("psid")
        or credentials.get("__Secure-1PSID")
        or ""
    ).strip()
    psidts = (
        credentials.get("secure_1psidts")
        or credentials.get("psidts")
        or credentials.get("__Secure-1PSIDTS")
        or ""
    ).strip()
    parts = [f"__Secure-1PSID={psid}"]
    if psidts:
        parts.append(f"__Secure-1PSIDTS={psidts}")
    return "; ".join(parts)


def _extract_at_token(html: str) -> Optional[str]:
    """Extract the SNlM0e / AT token from Gemini's initial page HTML."""
    m = re.search(r'"SNlM0e":"([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'SNlM0e["\\s]*:["\\s]*"([^"]+)"', html)
    return m.group(1) if m else None


def _extract_bl_token(html: str) -> Optional[str]:
    m = re.search(r'"cfb2h":"([^"]+)"', html)
    return m.group(1) if m else None


def _extract_session_id(html: str) -> Optional[str]:
    m = re.search(r'"FdrFJe":"([^"]+)"', html)
    return m.group(1) if m else None


def _build_inner_req_list(prompt: str) -> list:
    """
    Build the inner request list structure used in Gemini's StreamGenerate endpoint.
    Based on gemini-webapi v2.0.0 reverse engineering.
    """
    # Message content format: [prompt, 0, None, None, None, None, 0]
    message_content = [prompt, 0, None, None, None, None, 0]

    inner_req_list: List[Any] = [None] * 69
    inner_req_list[0] = message_content
    inner_req_list[1] = ["en"]
    inner_req_list[2] = _DEFAULT_METADATA
    inner_req_list[6] = [1]
    inner_req_list[_STREAMING_FLAG_INDEX] = 1
    inner_req_list[10] = 1
    inner_req_list[11] = 0
    inner_req_list[17] = [[0]]
    inner_req_list[18] = 0
    inner_req_list[27] = 1
    inner_req_list[30] = [4]
    inner_req_list[41] = [1]
    inner_req_list[53] = 0
    inner_req_list[61] = []
    inner_req_list[68] = 2

    uuid_val = str(uuid.uuid4()).upper()
    inner_req_list[59] = uuid_val

    return inner_req_list, uuid_val


def _parse_stream_chunks(raw: str) -> Optional[str]:
    """
    Parse the streaming response from Gemini's StreamGenerate endpoint.
    Extracts the assistant text from wrb.fr nested JSON chunks.
    
    The response structure is:
      [["wrb.fr", null, "<escaped_inner_json>"]]
    Where inner_json contains candidate objects with text arrays like:
      [..., [["rc_xxx", ["response text"], ...]], ...]
    """
    if not raw:
        return None

    best_text = None
    best_len = 0

    try:
        # Find all wrb.fr chunks and extract inner JSON strings
        inner_jsons = re.findall(r'\["wrb\.fr",null,"((?:[^"\\]|\\.)+?)"', raw)
        for ij in inner_jsons:
            try:
                inner_str = json.loads(f'"{ij}"')  # unescape the string
                inner = json.loads(inner_str)       # parse the JSON
                # Path: inner[4] contains candidates list
                candidates = inner[4] if (isinstance(inner, list) and len(inner) > 4) else None
                if not isinstance(candidates, list):
                    continue
                for candidate in candidates:
                    if not isinstance(candidate, list) or len(candidate) < 2:
                        continue
                    # candidate[1] is the text array
                    text_arr = candidate[1]
                    if isinstance(text_arr, list) and len(text_arr) > 0:
                        txt = text_arr[0]
                        if isinstance(txt, str) and len(txt) > best_len:
                            best_text = txt
                            best_len = len(txt)
            except Exception:
                continue
    except Exception:
        pass

    if best_text:
        return best_text

    # Fallback: find text near rc_ candidate ids in raw string
    try:
        rc_matches = re.finditer(r'"rc_[a-f0-9]+",\[("(?:[^"\\]|\\.)*")\]', raw)
        for m in rc_matches:
            try:
                txt = json.loads(m.group(1))
                if isinstance(txt, str) and len(txt) > best_len and len(txt) > 3:
                    best_text = txt
                    best_len = len(txt)
            except Exception:
                continue
    except Exception:
        pass

    if best_text:
        # Strip out <FollowUp> chips from Web UI
        best_text = re.sub(r'<FollowUp[^>]*/>', '', best_text)

    return best_text


class GeminiEngine(AbstractProvider):
    """
    Gemini provider engine.
    Auth: __Secure-1PSID cookie pair.
    Transport: curl-cffi with Chrome-120 TLS fingerprint.
    Request format: gemini-webapi v2.0.0 compatible.
    """

    provider_name = "gemini"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.request_timeout = config.get("request_timeout", 60)

    async def _get_session_tokens(
        self, session: AsyncSession, credentials: Dict[str, Any], account_id: int = 0
    ) -> tuple[str, str, str]:
        """Fetch AT token, BL token, and session ID from Gemini's main page."""
        cookie_header = _build_cookie_header(credentials)
        resp = await session.get(
            _INIT_URL,
            headers={
                "Cookie": cookie_header,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://gemini.google.com/",
            },
            impersonate="chrome120",
            timeout=30,
        )
        if resp.status_code in (401, 403):
            raise AuthError("Gemini cookie authentication failed. Please refresh cookies.")
        if resp.status_code != 200:
            raise ProviderError(f"Gemini init page returned {resp.status_code}", retry=True)

        html = resp.text
        at_token = _extract_at_token(html)
        bl_token = _extract_bl_token(html) or ""
        session_id = _extract_session_id(html) or ""

        # Auto-update credentials in DB if Google refreshed __Secure-1PSIDTS
        if resp.cookies.get("__Secure-1PSIDTS"):
            new_psidts = resp.cookies["__Secure-1PSIDTS"]
            if new_psidts and new_psidts != credentials.get("secure_1psidts"):
                credentials["secure_1psidts"] = new_psidts
                if account_id > 0:
                    from app.database import update_account_credentials
                    asyncio.create_task(update_account_credentials(account_id, credentials))
                    logger.info("Auto-refreshed __Secure-1PSIDTS for account #%d.", account_id)

        if not at_token:
            raise AuthError(
                "کوکی‌های اکانت Google Gemini منقضی شده‌اند.\n"
                "لطفاً وارد پنل مدیریت (http://localhost:8080/admin) ← مدیریت اکانت‌ها شده و کوکی جدید __Secure-1PSIDTS را کپی و ویرایش کنید."
            )
        return at_token, bl_token, session_id

    async def chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> str:
        chunks: List[str] = []
        async for chunk in self.stream_chat(request, account_id, credentials):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream_chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        cookie_header = _build_cookie_header(credentials)

        # Combine all messages into a single prompt
        parts: List[str] = []
        system_prompt = ""
        for msg in request.messages:
            if msg.role == "system":
                system_prompt = msg.text
            elif msg.role == "user":
                parts.append(msg.text)
            elif msg.role == "assistant":
                parts.append(f"[Assistant]: {msg.text}")
        prompt = "\n\n".join(parts)
        if system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"

        async with AsyncSession(impersonate="chrome120", timeout=self.request_timeout) as client:
            try:
                # Step 1: Get session tokens
                at_token, bl_token, session_id = await self._get_session_tokens(client, credentials, account_id)

                # Step 2: Build inner request list (gemini-webapi v2 format)
                inner_req_list, uuid_val = _build_inner_req_list(prompt)

                # Step 3: Build f.req payload
                f_req = json.dumps(
                    [None, json.dumps(inner_req_list, separators=(',', ':'), ensure_ascii=False)],
                    separators=(',', ':'),
                    ensure_ascii=False,
                )

                # Step 4: Build request params
                params: Dict[str, Any] = {
                    "hl": "en",
                    "_reqid": "1",
                    "rt": "c",
                }
                if bl_token:
                    params["bl"] = bl_token
                if session_id:
                    params["f.sid"] = session_id

                # Step 5: Build headers with model selection
                model_headers = _get_model_headers(request.model_id)
                request_headers = {
                    "Cookie": cookie_header,
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Origin": "https://gemini.google.com",
                    "Referer": "https://gemini.google.com/",
                    "X-Same-Domain": "1",
                    "x-goog-ext-525005358-jspb": f'["{uuid_val}",1]',
                    **model_headers,
                }

                form_data = {
                    "at": at_token,
                    "f.req": f_req,
                }

                # Step 6: Send streaming request
                resp = await client.post(
                    _GENERATE_URL,
                    params=params,
                    data=form_data,
                    headers=request_headers,
                    stream=True,
                    timeout=self.request_timeout,
                )

                if resp.status_code in (401, 403):
                    raise AuthError("کوکی‌های جمنای رد شدند. لطفاً آن‌ها را تازه‌سازی کنید.")
                if resp.status_code != 200:
                    raise ProviderError(f"خطای سرور جمنای: {resp.status_code}", retry=True)

                # Step 7: Stream and parse response
                collected = ""
                last_sent_len = 0
                async for chunk in resp.aiter_content():
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", "ignore")
                    collected += chunk
                    text = _parse_stream_chunks(collected)
                    if text and len(text) > last_sent_len:
                        new_part = text[last_sent_len:]
                        last_sent_len = len(text)
                        if new_part.strip():
                            yield new_part

                if last_sent_len == 0:
                    raise ProviderError("جمنای پاسخی برنگرداند. لطفاً مجدداً تلاش کنید.", retry=True)

            except (AuthError, RateLimitError, ProviderError):
                raise
            except Exception as e:
                raise ProviderError(f"خطا در شبکه جمنای: {e}")

    async def validate(self, credentials: Dict[str, Any]) -> bool:
        """Test Gemini credentials with AT token extraction from main page."""
        try:
            async with AsyncSession(impersonate="chrome120") as session:
                at_token, _, _ = await self._get_session_tokens(session, credentials, account_id=0)
                return bool(at_token)
        except Exception as e:
            logger.warning("Gemini validation failed: %s", e)
            return False
