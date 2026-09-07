"""
OmniBridge – Auth Middleware
Validates Bearer sk-omni-... API keys against the database.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import validate_api_key

_bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """
    FastAPI dependency – raises 401 if the API key is missing or invalid.
    Returns the validated key string on success. Also accepts admin session
    and x-api-key / anthropic-api-key headers for Anthropic compatibility.
    """
    session = request.session if hasattr(request, "session") else {}
    if session.get("admin_authenticated"):
        return "admin-session"

    key = None
    if credentials is not None:
        key = credentials.credentials

    if not key:
        # Fallback for Anthropic API clients passing x-api-key or anthropic-api-key
        key = request.headers.get("x-api-key") or request.headers.get("anthropic-api-key")

    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide Bearer token or x-api-key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    valid = await validate_api_key(key)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return key


async def require_admin(request: Request) -> None:
    """
    FastAPI dependency for admin routes.
    Checks session cookie set after successful login.
    """
    from app.config import settings  # avoid circular

    session = request.session if hasattr(request, "session") else {}
    if not session.get("admin_authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required.",
        )
