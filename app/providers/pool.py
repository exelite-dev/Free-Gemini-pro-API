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
    list_accounts,
    record_account_use,
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

    async def _get_accounts(self) -> List[Dict[str, Any]]:
        """Fetch enabled accounts from DB for this provider."""
        all_accounts = await list_accounts(self.provider_name)
        result = []
        for a in all_accounts:
            if not a.get("enabled"):
                continue
            creds = a.get("credentials")
            if isinstance(creds, str):
                try:
                    a["credentials"] = json.loads(creds)
                except Exception:
                    a["credentials"] = {}
            result.append(a)
        return result

    async def _pick_account(
        self, accounts: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Round-robin pick, skipping backed-off accounts."""
        now = time.time()
        n = len(accounts)
        if n == 0:
            return None

        async with self._lock:
            for _ in range(n):
                acct = accounts[self._index % n]
                self._index = (self._index + 1) % n
                backoff_until = self._backoff.get(acct["id"], 0)
                if now >= backoff_until:
                    return acct
        # All accounts are backed off — wait for the soonest to recover
        soonest = min(self._backoff.get(a["id"], 0) for a in accounts)
        wait = max(0, soonest - now) + 0.5
        logger.warning(
            "All %s accounts are backed off. Waiting %.1fs.", self.provider_name, wait
        )
        await asyncio.sleep(wait)
        return accounts[0]

    async def chat(self, request: UnifiedRequest) -> str:
        """Non-streaming chat with automatic failover."""
        accounts = await self._get_accounts()
        if not accounts:
            accounts = [{"id": 0, "label": "Guest Account", "credentials": {}, "enabled": True}]

        last_error: Optional[ProviderError] = None
        tried = set()

        for _ in range(len(accounts)):
            acct = await self._pick_account(accounts)
            if acct is None or acct["id"] in tried:
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
                logger.warning("Account %d rate-limited. Backing off.", acct["id"])
                self._backoff[acct["id"]] = time.time() + _RATE_LIMIT_BACKOFF
                if acct["id"] != 0:
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
        raise ProviderError(f"All {self.provider_name} accounts failed.", status_code=503)

    async def stream_chat(
        self, request: UnifiedRequest
    ) -> AsyncGenerator[str, None]:
        """Streaming chat with automatic failover."""
        accounts = await self._get_accounts()
        if not accounts:
            accounts = [{"id": 0, "label": "Guest Account", "credentials": {}, "enabled": True}]

        last_error: Optional[ProviderError] = None
        tried = set()

        for _ in range(len(accounts)):
            acct = await self._pick_account(accounts)
            if acct is None or acct["id"] in tried:
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
                logger.warning("Account %d rate-limited. Backing off.", acct["id"])
                self._backoff[acct["id"]] = time.time() + _RATE_LIMIT_BACKOFF
                if acct["id"] != 0:
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
        raise ProviderError(f"All {self.provider_name} accounts failed.", status_code=503)
