"""
OmniBridge – Provider Registry
Central registry that wires engines → pools and exposes
get_pool(provider) for use in route handlers.
"""
from __future__ import annotations

from typing import Dict, Optional

from app.providers.base import AbstractProvider
from app.providers.pool import AccountPool

_pools: Dict[str, AccountPool] = {}


def register_provider(provider_name: str, engine: AbstractProvider) -> None:
    _pools[provider_name] = AccountPool(provider_name, engine)


def get_pool(provider_name: str) -> Optional[AccountPool]:
    return _pools.get(provider_name)


def all_pools() -> Dict[str, AccountPool]:
    return dict(_pools)
