"""
OmniBridge – Database
Async SQLite (aiosqlite) schema and helper layer.
Tables: api_keys, accounts, provider_config, request_metrics
"""
from __future__ import annotations

import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiosqlite

from app.config import settings

_DB_PATH: str = settings.database_path


# ─────────────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_value   TEXT    NOT NULL UNIQUE,
    label       TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    last_used   REAL
);

CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,   -- 'gemini' | 'qwen' | 'copilot' | 'glm'
    label       TEXT    NOT NULL DEFAULT '',
    credentials TEXT    NOT NULL,   -- JSON blob
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_used   REAL,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_config (
    provider    TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS request_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    provider    TEXT    NOT NULL,
    model_id    TEXT    NOT NULL,
    success     INTEGER NOT NULL DEFAULT 1,
    latency_ms  REAL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_metrics_ts ON request_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_provider ON request_metrics(provider);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    """Create tables and seed default provider config rows."""
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with get_db() as db:
        await db.executescript(_SCHEMA)
        # Seed provider config rows if missing
        for provider, enabled in [
            ("gemini", int(settings.gemini_enabled)),
        ]:
            await db.execute(
                """
                INSERT OR IGNORE INTO provider_config (provider, enabled, updated_at)
                VALUES (?, ?, ?)
                """,
                (provider, enabled, time.time()),
            )
        # Delete non-gemini provider accounts and config
        await db.execute("DELETE FROM accounts WHERE provider != 'gemini'")
        await db.execute("DELETE FROM provider_config WHERE provider != 'gemini'")

        # Seed default API keys if none exist
        async with db.execute("SELECT COUNT(*) as cnt FROM api_keys") as cur:
            row = await cur.fetchone()
            if row and row["cnt"] == 0:
                now = time.time()
                for key_val, label in [("sk-test", "Default Test Key"), ("sk-omni-default", "Default Omni Key")]:
                    await db.execute(
                        "INSERT INTO api_keys (key_value, label, created_at) VALUES (?, ?, ?)",
                        (key_val, label, now),
                    )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Provider Config
# ─────────────────────────────────────────────────────────────────────────────

async def is_provider_enabled(provider: str) -> bool:
    if provider != "gemini":
        return False
    async with get_db() as db:
        async with db.execute(
            "SELECT enabled FROM provider_config WHERE provider = ?", (provider,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row["enabled"]) if row else True

get_provider_enabled = is_provider_enabled


async def set_provider_enabled(provider: str, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO provider_config (provider, enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at
            """,
            (provider, int(enabled), time.time()),
        )
        await db.commit()


async def get_all_provider_states() -> Dict[str, bool]:
    async with get_db() as db:
        async with db.execute("SELECT provider, enabled FROM provider_config") as cur:
            rows = await cur.fetchall()
            db_states = {row["provider"]: bool(row["enabled"]) for row in rows}
            all_providers = ["gemini"]
            return {p: db_states.get(p, True) for p in all_providers}


# ─────────────────────────────────────────────────────────────────────────────
# API Keys
# ─────────────────────────────────────────────────────────────────────────────

def _generate_api_key() -> str:
    token = secrets.token_urlsafe(32)
    return f"sk-omni-{token}"


async def create_api_key(label: str = "") -> Dict[str, Any]:
    key = _generate_api_key()
    now = time.time()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO api_keys (key_value, label, created_at) VALUES (?, ?, ?)",
            (key, label, now),
        )
        await db.commit()
    return {"key": key, "label": label, "created_at": now}


async def validate_api_key(key: str) -> bool:
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM api_keys WHERE key_value = ? AND revoked = 0", (key,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                await db.execute(
                    "UPDATE api_keys SET last_used = ? WHERE key_value = ?",
                    (time.time(), key),
                )
                await db.commit()
                return True
    return False


async def list_api_keys() -> List[Dict[str, Any]]:
    async with get_db() as db:
        async with db.execute(
            "SELECT id, key_value, label, created_at, revoked, last_used FROM api_keys ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def revoke_api_key(key_id: int) -> bool:
    async with get_db() as db:
        await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        await db.commit()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Accounts
# ─────────────────────────────────────────────────────────────────────────────

async def add_account(provider: str, label: str, credentials: Any) -> int:
    now = time.time()
    if isinstance(credentials, str):
        try:
            credentials = json.loads(credentials)
        except Exception:
            credentials = {"token": credentials}
    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO accounts (provider, label, credentials, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (provider, label, json.dumps(credentials), now),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def list_accounts(provider: Optional[str] = None) -> List[Dict[str, Any]]:
    async with get_db() as db:
        if provider:
            async with db.execute(
                "SELECT * FROM accounts WHERE provider = ? ORDER BY created_at",
                (provider,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM accounts ORDER BY provider, created_at"
            ) as cur:
                rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw_creds = d.get("credentials", "{}")
            try:
                creds = json.loads(raw_creds)
                if isinstance(creds, str):
                    creds = json.loads(creds)
            except Exception:
                creds = {}
            if not isinstance(creds, dict):
                creds = {}
            d["credentials_masked"] = _mask_credentials(creds)
            d["credentials"] = creds
            result.append(d)
        return result


def _mask_credentials(creds: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(creds, dict):
        return {}
    masked = {}
    for k, v in creds.items():
        s = str(v)
        masked[k] = s[:4] + "*" * (len(s) - 8) + s[-4:] if len(s) > 8 else "****"
    return masked


async def update_account_credentials(
    account_id: int, credentials: Dict[str, Any]
) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET credentials = ? WHERE id = ?",
            (json.dumps(credentials), account_id),
        )
        await db.commit()


async def update_account(
    account_id: int, label: str, credentials: Dict[str, Any]
) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET label = ?, credentials = ? WHERE id = ?",
            (label, json.dumps(credentials), account_id),
        )
        await db.commit()


async def set_account_enabled(account_id: int, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET enabled = ? WHERE id = ?", (int(enabled), account_id)
        )
        await db.commit()


async def delete_account(account_id: int) -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        await db.commit()


async def record_account_use(account_id: int, failed: bool = False) -> None:
    async with get_db() as db:
        if failed:
            await db.execute(
                "UPDATE accounts SET last_used=?, fail_count=fail_count+1 WHERE id=?",
                (time.time(), account_id),
            )
        else:
            await db.execute(
                "UPDATE accounts SET last_used=?, fail_count=0 WHERE id=?",
                (time.time(), account_id),
            )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

async def record_metric(
    provider: str,
    model_id: str,
    success: bool,
    latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO request_metrics
                (ts, provider, model_id, success, latency_ms, input_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                provider,
                model_id,
                int(success),
                latency_ms,
                input_tokens,
                output_tokens,
            ),
        )
        await db.commit()


async def get_metrics_summary(since_seconds: int = 3600) -> Dict[str, Any]:
    """Return aggregate metrics for the last N seconds."""
    cutoff = time.time() - since_seconds
    async with get_db() as db:
        async with db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(success) as successes,
                AVG(latency_ms) as avg_latency,
                provider
            FROM request_metrics
            WHERE ts > ?
            GROUP BY provider
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
            per_provider = {}
            for r in rows:
                per_provider[r["provider"]] = {
                    "total": r["total"],
                    "successes": r["successes"],
                    "avg_latency_ms": round(r["avg_latency"] or 0, 2),
                    "success_rate": round((r["successes"] / r["total"] * 100) if r["total"] else 0, 1),
                }

        # Time-series buckets (last 60 minutes, per-minute)
        async with db.execute(
            """
            SELECT
                CAST((ts - ?) / 60 AS INTEGER) as bucket,
                COUNT(*) as requests,
                SUM(success) as ok
            FROM request_metrics
            WHERE ts > ?
            GROUP BY bucket
            ORDER BY bucket
            """,
            (cutoff, cutoff),
        ) as cur:
            rows = await cur.fetchall()
            time_series = [
                {"bucket": r["bucket"], "requests": r["requests"], "ok": r["ok"]}
                for r in rows
            ]

    return {"per_provider": per_provider, "time_series": time_series}
