"""
OmniBridge – Abstract Provider Base
All provider engines inherit from AbstractProvider.
"""
from __future__ import annotations

import abc
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.models import UnifiedRequest


class ProviderError(Exception):
    """Base exception for provider-level errors."""
    def __init__(self, message: str, status_code: int = 500, retry: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retry = retry


class RateLimitError(ProviderError):
    """Provider is rate-limiting this account."""
    def __init__(self, message: str = "Rate limited"):
        super().__init__(message, status_code=429, retry=True)


class AuthError(ProviderError):
    """Authentication/cookie/token failure."""
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401, retry=False)


class AbstractProvider(abc.ABC):
    """
    Base class for all OmniBridge provider engines.
    Sub-classes must implement:
      - chat()         — non-streaming, returns full text
      - stream_chat()  — async generator yielding text chunks
      - validate()     — test that credentials work; raise ProviderError on failure
    """

    provider_name: str = "unknown"

    @abc.abstractmethod
    async def chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> str:
        """
        Send a chat request and return the complete response text.
        Raises ProviderError subclasses on failure.
        """

    @abc.abstractmethod
    async def stream_chat(
        self,
        request: UnifiedRequest,
        account_id: int,
        credentials: Dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        Send a chat request and yield response text in chunks.
        Raises ProviderError subclasses on failure.
        """
        # Must be implemented as an async generator
        yield ""  # pragma: no cover

    @abc.abstractmethod
    async def validate(self, credentials: Dict[str, Any]) -> bool:
        """
        Validate that the provided credentials work.
        Returns True on success.
        Raises ProviderError on failure.
        """
