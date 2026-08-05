"""
OmniBridge Unit & Integration Test Suite
"""
import pytest
import asyncio
from fastapi.testclient import TestClient

from app.main import create_app
from app.database import init_db, validate_api_key, create_api_key, add_account, list_accounts
from app.providers.pool import AccountPool
from app.providers.base import AbstractProvider, ProviderError
from app.models import UnifiedRequest, UnifiedMessage


class DummyProvider(AbstractProvider):
    provider_name = "dummy"

    async def chat(self, request: UnifiedRequest, account_id: int, credentials: dict) -> str:
        if credentials.get("fail"):
            raise ProviderError("Simulated failure", retry=True)
        return f"Response for {account_id}: {request.messages[-1].text}"

    async def stream_chat(self, request: UnifiedRequest, account_id: int, credentials: dict):
        if credentials.get("fail"):
            raise ProviderError("Simulated failure", retry=True)
        yield f"Stream chunk for {account_id}"

    async def validate(self, credentials: dict) -> bool:
        return not credentials.get("fail", False)


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_omnibridge.db")
    monkeypatch.setattr("app.database._DB_PATH", db_file)
    asyncio.run(init_db())


def test_root_and_health():
    app = create_app()
    with TestClient(app) as client:
        r_root = client.get("/")
        assert r_root.status_code == 200
        assert r_root.json()["name"] == "OmniBridge"

        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert "status" in r_health.json()
        assert r_health.json()["status"] == "ok"


def test_api_keys_and_validation():
    async def _test():
        # Default test key
        valid = await validate_api_key("sk-test")
        assert valid is True

        invalid = await validate_api_key("sk-invalid-key")
        assert invalid is False

        new_key = await create_api_key("New Test Key")
        assert new_key["key"].startswith("sk-omni-")
        assert await validate_api_key(new_key["key"]) is True

    asyncio.run(_test())


def test_x_api_key_header_support():
    app = create_app()
    with TestClient(app) as client:
        # Test x-api-key header (Anthropic standard)
        r = client.get("/v1/models", headers={"x-api-key": "sk-test"})
        assert r.status_code == 200

        # Test anthropic-api-key header
        r2 = client.get("/v1/models", headers={"anthropic-api-key": "sk-test"})
        assert r2.status_code == 200


def test_account_pool_string_credentials_and_failover():
    async def _test():
        engine = DummyProvider()
        pool = AccountPool("dummy", engine)

        # Add two accounts: one stored as a JSON string, one as a dict
        await add_account("dummy", "Acct 1 (Fail)", {"fail": True})
        await add_account("dummy", "Acct 2 (OK)", '{"fail": false}')

        req = UnifiedRequest(
            model_id="dummy-model",
            provider="dummy",
            messages=[UnifiedMessage(role="user", text="Hello")],
        )

        # Should failover past account 1 and return response from account 2
        res = await pool.chat(req)
        assert "Response for" in res

    asyncio.run(_test())


def test_models_endpoint_all_providers():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]

        # Gemini Provider models
        assert "gemini-3.5-flash-lite" in model_ids
        assert "gemini-3.6-flash" in model_ids
        assert "gemini-3.1-pro" in model_ids
        assert "gemini-pro-extended" in model_ids
        assert len(model_ids) >= 8


def test_provider_toggles():
    app = create_app()
    with TestClient(app) as client:
        # Login as admin to get session
        login_res = client.post("/admin/api/login", json={"username": "admin", "password": "changeme"})
        assert login_res.status_code == 200

        # Toggle Gemini provider
        for provider in ("gemini",):
            res = client.post(f"/admin/api/providers/{provider}/toggle", json={"enabled": False})
            assert res.status_code == 200
            assert res.json()["enabled"] is False

        # Verify admin/api/models reflects toggle
        res_models = client.get("/admin/api/models")
        assert res_models.status_code == 200
        assert len(res_models.json()) == 0


def test_unauthorized_request():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer invalid-token"})
        assert r.status_code == 401
