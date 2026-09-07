"""
OmniBridge – Admin API Routes
REST endpoints for the admin dashboard UI.
All routes are prefixed /admin/api/...
Session-cookie protected (login via /admin/login).
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.database import (
    add_account,
    create_api_key,
    delete_account,
    get_all_provider_states,
    get_metrics_summary,
    list_accounts,
    list_api_keys,
    record_metric,
    revoke_api_key,
    set_account_enabled,
    set_provider_enabled,
    update_account,
)
from app.providers.base import ProviderError
from app.providers.registry import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()

import os
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "admin", "templates")
templates = Jinja2Templates(directory=_TEMPLATE_DIR)

# ── Session helpers ──────────────────────────────────────────────────────────

def _is_admin(request: Request) -> bool:
    return request.session.get("admin_authenticated", False)


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Admin login required.")


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/admin/api/login")
async def admin_login(body: LoginRequest, request: Request):
    if (
        body.username == settings.admin_username
        and body.password == settings.admin_password
    ):
        request.session["admin_authenticated"] = True
        return {"success": True}
    raise HTTPException(status_code=401, detail="Invalid credentials.")


@router.post("/admin/api/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return {"success": True}


@router.get("/admin/api/auth-check")
async def auth_check(request: Request):
    return {"authenticated": _is_admin(request)}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/admin/api/metrics")
async def get_metrics(request: Request, window: int = 3600):
    _require_admin(request)
    summary = await get_metrics_summary(since_seconds=window)
    provider_states = await get_all_provider_states()
    return {
        "metrics": summary,
        "providers": provider_states,
    }


# ── Provider Toggles ─────────────────────────────────────────────────────────

class ProviderToggle(BaseModel):
    enabled: bool


@router.post("/admin/api/providers/{provider}/toggle")
async def toggle_provider(provider: str, body: ProviderToggle, request: Request):
    _require_admin(request)
    if provider not in ("gemini", "deepseek", "chatgpt"):
        raise HTTPException(status_code=400, detail="Unknown provider.")
    await set_provider_enabled(provider, body.enabled)
    return {"provider": provider, "enabled": body.enabled}


@router.get("/admin/api/providers")
async def get_providers(request: Request):
    _require_admin(request)
    states = await get_all_provider_states()
    return states


# ── Account Management ───────────────────────────────────────────────────────

class AddAccountRequest(BaseModel):
    provider: str
    label: str
    credentials: Dict[str, Any]


class AccountToggle(BaseModel):
    enabled: bool


@router.get("/admin/api/accounts")
async def get_accounts(request: Request, provider: Optional[str] = None):
    _require_admin(request)
    accounts = await list_accounts(provider)
    # Mask credentials before returning
    for acct in accounts:
        acct.pop("credentials", None)
    return accounts


@router.post("/admin/api/accounts")
async def add_account_route(body: AddAccountRequest, request: Request):
    _require_admin(request)
    if body.provider not in ("gemini", "deepseek", "chatgpt"):
        raise HTTPException(status_code=400, detail="Unknown provider.")
    acct_id = await add_account(body.provider, body.label, body.credentials)
    return {"id": acct_id, "provider": body.provider, "label": body.label}


@router.post("/admin/api/accounts/{account_id}/toggle")
async def toggle_account(account_id: int, body: AccountToggle, request: Request):
    _require_admin(request)
    await set_account_enabled(account_id, body.enabled)
    return {"id": account_id, "enabled": body.enabled}


@router.delete("/admin/api/accounts/{account_id}")
async def remove_account(account_id: int, request: Request):
    _require_admin(request)
    await delete_account(account_id)
    return {"deleted": account_id}


class UpdateAccountRequest(BaseModel):
    label: str
    credentials: Dict[str, Any]


@router.put("/admin/api/accounts/{account_id}")
async def update_account_route(account_id: int, body: UpdateAccountRequest, request: Request):
    _require_admin(request)
    await update_account(account_id, body.label, body.credentials)
    return {"updated": account_id}


@router.post("/admin/api/accounts/{account_id}/test")
async def test_account(account_id: int, request: Request):
    """Perform a real validation call on the account."""
    _require_admin(request)
    accounts = await list_accounts()
    acct = next((a for a in accounts if a["id"] == account_id), None)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found.")

    provider_name = acct["provider"]
    pool = get_pool(provider_name)
    if not pool:
        raise HTTPException(status_code=503, detail=f"Provider '{provider_name}' not running.")

    try:
        valid = await pool.engine.validate(acct["credentials"])
        if not valid:
            return {"success": False, "account_id": account_id, "error": "توکن یا کوکی‌های این اکانت نامعتبر است یا انقضا یافته است."}
        return {"success": True, "account_id": account_id}
    except ProviderError as e:
        return {"success": False, "account_id": account_id, "error": str(e)}
    except Exception as e:
        return {"success": False, "account_id": account_id, "error": str(e)}


# ── API Key Management ───────────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    label: str = ""


@router.get("/admin/api/keys")
async def get_keys(request: Request):
    _require_admin(request)
    keys = await list_api_keys()
    return keys


@router.post("/admin/api/keys")
async def create_key(body: CreateKeyRequest, request: Request):
    _require_admin(request)
    result = await create_api_key(body.label)
    return result


@router.delete("/admin/api/keys/{key_id}")
async def delete_key(key_id: int, request: Request):
    _require_admin(request)
    await revoke_api_key(key_id)
    return {"revoked": key_id}


# ── Playground passthrough ──────────────────────────────────────────────────

@router.get("/admin/api/models")
async def admin_models(request: Request):
    """Return available models for the playground (uses admin session, no API key)."""
    _require_admin(request)
    provider_states = await get_all_provider_states()
    models = settings.get_all_models(
        gemini_enabled=provider_states.get("gemini", True),
        deepseek_enabled=provider_states.get("deepseek", True),
        chatgpt_enabled=provider_states.get("chatgpt", True),
    )
    return [{"id": m.id, "display_name": m.display_name, "provider": m.provider} for m in models]


# ── Serve Admin SPA ──────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
