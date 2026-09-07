"""
OmniBridge – Configuration
Single source-of-truth: reads from .env → config.yaml → defaults.
Provider toggles can also be overridden at runtime via the admin DB.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────────────────────────────────────
# Helper – load yaml
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: str) -> Dict[str, Any]:
    p = Path(path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Model definitions (static, read from yaml)
# ─────────────────────────────────────────────────────────────────────────────

class ModelDefinition:
    def __init__(self, id: str, display_name: str, context_window: int,
                 supports_vision: bool, provider: str):
        self.id = id
        self.display_name = display_name
        self.context_window = context_window
        self.supports_vision = supports_vision
        self.provider = provider

    def to_openai_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "object": "model",
            "created": 1700000000,
            "owned_by": f"omnibridge-{self.provider}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Database
    database_path: str = "./data/omnibridge.db"

    # Admin
    admin_username: str = "admin"
    admin_password: str = "changeme"
    admin_secret_key: str = "supersecretkey-change-in-production"

    # Provider toggles (env-level defaults; can be overridden in DB at runtime)
    gemini_enabled: bool = True
    deepseek_enabled: bool = True
    chatgpt_enabled: bool = True

    # Config file path
    config_file: str = "./config.yaml"

    # ── Internal state (populated from yaml on init) ───────────────────────
    _yaml_cfg: Dict[str, Any] = {}
    _gemini_models: List[ModelDefinition] = []
    _deepseek_models: List[ModelDefinition] = []
    _chatgpt_models: List[ModelDefinition] = []

    def model_post_init(self, __context: Any) -> None:
        self._yaml_cfg = _load_yaml(self.config_file)

        # Merge yaml settings if not explicitly set via environment
        server_cfg = self._yaml_cfg.get("server", {})
        if "HOST" not in os.environ and "host" in server_cfg:
            self.host = str(server_cfg["host"])
        if "PORT" not in os.environ and "port" in server_cfg:
            self.port = int(server_cfg["port"])
        if "LOG_LEVEL" not in os.environ and "log_level" in server_cfg:
            self.log_level = str(server_cfg["log_level"])

        db_cfg = self._yaml_cfg.get("database", {})
        if "DATABASE_PATH" not in os.environ and "path" in db_cfg:
            self.database_path = str(db_cfg["path"])

        admin_cfg = self._yaml_cfg.get("admin", {})
        if "ADMIN_USERNAME" not in os.environ and "username" in admin_cfg:
            self.admin_username = str(admin_cfg["username"])
        if "ADMIN_PASSWORD" not in os.environ and "password" in admin_cfg:
            self.admin_password = str(admin_cfg["password"])
        if "ADMIN_SECRET_KEY" not in os.environ and "secret_key" in admin_cfg:
            self.admin_secret_key = str(admin_cfg["secret_key"])

        self._load_models()

    def _load_models(self) -> None:
        """Parse model definitions from config.yaml."""
        providers = self._yaml_cfg.get("providers", {})

        gemini_cfg = providers.get("gemini", {})
        self._gemini_models = [
            ModelDefinition(
                id=m["id"],
                display_name=m.get("display_name", m["id"]),
                context_window=m.get("context_window", 1000000),
                supports_vision=m.get("supports_vision", True),
                provider="gemini",
            )
            for m in gemini_cfg.get("models", [])
        ]

        deepseek_cfg = providers.get("deepseek", {})
        self._deepseek_models = [
            ModelDefinition(
                id=m["id"],
                display_name=m.get("display_name", m["id"]),
                context_window=m.get("context_window", 64000),
                supports_vision=m.get("supports_vision", False),
                provider="deepseek",
            )
            for m in deepseek_cfg.get("models", [])
        ]

        chatgpt_cfg = providers.get("chatgpt", {})
        self._chatgpt_models = [
            ModelDefinition(
                id=m["id"],
                display_name=m.get("display_name", m["id"]),
                context_window=m.get("context_window", 128000),
                supports_vision=m.get("supports_vision", True),
                provider="chatgpt",
            )
            for m in chatgpt_cfg.get("models", [])
        ]

    # ── Provider config helpers ────────────────────────────────────────────

    def get_gemini_config(self) -> Dict[str, Any]:
        return self._yaml_cfg.get("providers", {}).get("gemini", {})

    def get_deepseek_config(self) -> Dict[str, Any]:
        return self._yaml_cfg.get("providers", {}).get("deepseek", {})

    def get_chatgpt_config(self) -> Dict[str, Any]:
        return self._yaml_cfg.get("providers", {}).get("chatgpt", {})

    # ── Runtime model list (checked against DB toggle at call-time) ────────

    def get_all_models(
        self,
        gemini_enabled: Optional[bool] = None,
        deepseek_enabled: Optional[bool] = None,
        chatgpt_enabled: Optional[bool] = None,
    ) -> List[ModelDefinition]:
        """
        Return models for enabled providers.
        """
        g_on = gemini_enabled if gemini_enabled is not None else self.gemini_enabled
        ds_on = deepseek_enabled if deepseek_enabled is not None else self.deepseek_enabled
        cg_on = chatgpt_enabled if chatgpt_enabled is not None else self.chatgpt_enabled

        result: List[ModelDefinition] = []
        if g_on:
            result.extend(self._gemini_models)
        if ds_on:
            result.extend(self._deepseek_models)
        if cg_on:
            result.extend(self._chatgpt_models)
        return result

    def get_model_by_id(
        self,
        model_id: str,
        gemini_enabled: Optional[bool] = None,
        deepseek_enabled: Optional[bool] = None,
        chatgpt_enabled: Optional[bool] = None,
    ) -> Optional[ModelDefinition]:
        for m in self.get_all_models(gemini_enabled, deepseek_enabled, chatgpt_enabled):
            if m.id == model_id:
                return m
        return None


settings = Settings()
