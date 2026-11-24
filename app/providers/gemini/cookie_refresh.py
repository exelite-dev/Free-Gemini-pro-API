"""
OmniBridge – Gemini Cookie Auto-Refresh
Background asyncio task that monitors cookies and refreshes them
before they expire (default: every 55 minutes).
Persists updated cookies back to the accounts DB table.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict

from app.database import list_accounts, update_account_credentials

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = 3300  # 55 minutes (cookies expire ~60 min)
_RUNNING = False


async def _refresh_single_account(account: Dict[str, Any]) -> None:
    """
    Actively ping Gemini with account cookies, auto-persisting updated __Secure-1PSIDTS.
    Skips ping if the account was active recently (within 25 mins) to avoid bot detection.
    """
    from app.providers.gemini.engine import GeminiEngine
    from app.config import settings
    from curl_cffi.requests import AsyncSession

    # If used recently, the user's natural requests already kept cookies fresh
    last_used = account.get("last_used") or 0
    if time.time() - last_used < 1500:
        logger.info(
            "Gemini account %d (%s) was active recently (%.0fs ago); skipping heartbeat ping.",
            account["id"],
            account["label"],
            time.time() - last_used,
        )
        return

    credentials = account["credentials"]
    proxy = credentials.get("proxy") or settings.gemini_proxy
    engine = GeminiEngine(config=settings.get_gemini_config())

    try:
        async with AsyncSession(impersonate="chrome131", proxy=proxy) as session:
            await engine._get_session_tokens(
                session, credentials, account_id=account["id"], force_refresh=True
            )
            logger.info(
                "Gemini account %d (%s): heartbeat successful & cookies refreshed.",
                account["id"],
                account["label"],
            )
    except Exception as e:
        logger.warning(
            "Gemini account %d (%s): heartbeat check: %s",
            account["id"],
            account["label"],
            e,
        )


async def cookie_refresh_loop(interval: int = _REFRESH_INTERVAL) -> None:
    """
    Perpetually running background task with de-synchronized jittered intervals.
    Validates cookies sequentially with humanized pauses between accounts.
    """
    global _RUNNING
    _RUNNING = True
    logger.info("Gemini cookie refresh loop started (interval=%ds).", interval)

    while _RUNNING:
        try:
            accounts = await list_accounts("gemini")
            enabled = [a for a in accounts if a["enabled"]]
            if enabled:
                logger.info(
                    "Checking cookies for %d Gemini account(s) sequentially...", len(enabled)
                )
                for acct in enabled:
                    if not _RUNNING:
                        break
                    await _refresh_single_account(acct)
                    # Random delay between accounts to avoid simultaneous bursts
                    await asyncio.sleep(random.uniform(3.0, 10.0))
        except Exception as e:
            logger.error("Cookie refresh loop error: %s", e)

        # Add jitter (+/- 3 minutes) so Google doesn't see clockwork regularity
        jittered_sleep = max(600, interval + random.uniform(-180, 180))
        await asyncio.sleep(jittered_sleep)


def stop_cookie_refresh_loop() -> None:
    global _RUNNING
    _RUNNING = False
    logger.info("Gemini cookie refresh loop stopped.")
