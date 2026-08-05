"""
OmniBridge – Account Pool
Round-robin load balancing with automatic failover.
Each provider gets its own AccountPool instance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.database import (
    get_available_account,
    record_account_use,
    set_account_cooldown,
)
from app.models import UnifiedRequest
from app.providers.base import AbstractProvider, ProviderError, RateLimitError

logger = logging.getLogger(__name__)


# How long to back off a failed account before retrying (seconds)
_RATE_LIMIT_BACKOFF = 60
_MAX_FAIL_COUNT = 3        # disable account after this many consecutive failures


class AccountPool:
    """
    Manages a pool of accounts for one provider.
    Provides round-robin dispatch with automatic failover on rate-limit.
    """

    def __init__(self, provider_name: str, engine: AbstractProvider):
        self.provider_name = provider_name
        self.engine = engine
        self._lock = asyncio.Lock()
        self._index = 0
        self._backoff: Dict[int, float] = {}   # account_id → available-after timestamp

    async def _pick_account(self) -> Optional[Dict[str, Any]]:
        """Round-robin pick from the database, skipping accounts in cooldown."""
        acct = await get_available_account(self.provider_name)
        if not acct:
            logger.warning("No available accounts for %s (all in cooldown or disabled).", self.provider_name)
            await asyncio.sleep(1)
        return acct

    async def chat(self, request: UnifiedRequest) -> str:
        """Non-streaming chat with automatic failover."""
        last_error: Optional[ProviderError] = None
        tried = set()

        # Try up to _MAX_FAIL_COUNT times
        for _ in range(_MAX_FAIL_COUNT):
            acct = await self._pick_account()
            if acct is None:
                if not tried:
                    # If absolutely no accounts exist, use guest fallback
                    acct = {"id": 0, "label": "Guest Account", "credentials": {}, "enabled": True}
                else:
                    break

            if acct["id"] in tried:
                break
            tried.add(acct["id"])

            try:
                result = await self.engine.chat(
                    request, acct["id"], acct["credentials"]
                )
                if acct["id"] != 0:
                    await record_account_use(acct["id"], failed=False)
                return result
            except RateLimitError as e:
                logger.warning("Account %d rate-limited. Putting in DB cooldown.", acct["id"])
                if acct["id"] != 0:
                    await set_account_cooldown(acct["id"], minutes=1)
                    await record_account_use(acct["id"], failed=True)
                last_error = e
            except ProviderError as e:
                if not e.retry:
                    raise
                logger.warning("Account %d error: %s. Trying next.", acct["id"], e)
                if acct["id"] != 0:
                    await record_account_use(acct["id"], failed=True)
                last_error = e

        if last_error:
            raise last_error
        raise ProviderError(f"All {self.provider_name} accounts failed or are in cooldown.", status_code=503)

    async def stream_chat(
        self, request: UnifiedRequest
    ) -> AsyncGenerator[str, None]:
        """Streaming chat with automatic failover."""
        last_error: Optional[ProviderError] = None
        tried = set()

        # Try up to _MAX_FAIL_COUNT times
        for _ in range(_MAX_FAIL_COUNT):
            acct = await self._pick_account()
            if acct is None:
                if not tried:
                    acct = {"id": 0, "label": "Guest Account", "credentials": {}, "enabled": True}
                else:
                    break

            if acct["id"] in tried:
                break
            tried.add(acct["id"])

            try:
                async for chunk in self.engine.stream_chat(
                    request, acct["id"], acct["credentials"]
                ):
                    yield chunk
                if acct["id"] != 0:
                    await record_account_use(acct["id"], failed=False)
                return
            except RateLimitError as e:
                logger.warning("Account %d rate-limited. Putting in DB cooldown.", acct["id"])
                if acct["id"] != 0:
                    await set_account_cooldown(acct["id"], minutes=1)
                    await record_account_use(acct["id"], failed=True)
                last_error = e
            except ProviderError as e:
                if not e.retry:
                    raise
                logger.warning("Account %d error: %s. Trying next.", acct["id"], e)
                if acct["id"] != 0:
                    await record_account_use(acct["id"], failed=True)
                last_error = e

        if last_error:
            raise last_error
        raise ProviderError(f"All {self.provider_name} accounts failed or are in cooldown.", status_code=503)
