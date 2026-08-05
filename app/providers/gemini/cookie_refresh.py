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
import time
from typing import Any, Dict

from app.database import list_accounts, update_account_credentials

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = 3300  # 55 minutes (cookies expire ~60 min)
_RUNNING = False


async def _refresh_single_account(account: Dict[str, Any]) -> None:
    """
    Actively ping Gemini with account cookies, auto-persisting updated __Secure-1PSIDTS.
    """
    from app.providers.gemini.engine import GeminiEngine
    from app.config import settings
    from curl_cffi.requests import AsyncSession

    credentials = account["credentials"]
    engine = GeminiEngine(config=settings.get_gemini_config())

    try:
        async with AsyncSession(impersonate="chrome120") as session:
            await engine._get_session_tokens(session, credentials)
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
    Perpetually running background task.
    Every `interval` seconds, validates cookies for all enabled Gemini accounts.
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
                    "Refreshing cookies for %d Gemini account(s).", len(enabled)
                )
                tasks = [_refresh_single_account(a) for a in enabled]
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error("Cookie refresh loop error: %s", e)

        await asyncio.sleep(interval)


def stop_cookie_refresh_loop() -> None:
    global _RUNNING
    _RUNNING = False
    logger.info("Gemini cookie refresh loop stopped.")
