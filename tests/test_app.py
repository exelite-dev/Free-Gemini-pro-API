"""
OmniBridge Unit & Integration Test Suite
Comprehensive testing for Gemini, DeepSeek (with PoW solver & reasoning_content),
ChatGPT, Account Pooling, and Multi-Provider routing.
"""
import pytest
import asyncio
import base64
import json
from fastapi.testclient import TestClient

from app.main import create_app
from app.database import init_db, validate_api_key, create_api_key, add_account, list_accounts
from app.providers.pool import AccountPool
from app.providers.base import AbstractProvider, ProviderError
from app.models import UnifiedRequest, UnifiedMessage
from app.providers.deepseek.pow_solver import (
    deepseek_hash_v1,
    solve_pow_challenge,
    encode_pow_response,
)


class DummyProvider(AbstractProvider):
    provider_name = "dummy"

    async def chat(self, request: UnifiedRequest, account_id: int, credentials: dict) -> str:
        if credentials.get("fail"):
            raise ProviderError("Simulated failure", retry=True)
        return f"Response for {account_id}: {request.messages[-1].text}"

    async def stream_chat(self, request: UnifiedRequest, account_id: int, credentials: dict):
        if credentials.get("fail"):
            raise ProviderError("Simulated failure", retry=True)
        if "reasoner" in request.model_id.lower():
            yield {"reasoning_content": "Thinking step by step..."}
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
        assert "gemini" in r_health.json()["providers"]
        assert "deepseek" in r_health.json()["providers"]
        assert "chatgpt" in r_health.json()["providers"]


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
        assert "gemini-2.5-pro" in model_ids
        assert "gemini-2.5-flash" in model_ids
        assert "gemini-2.0-flash-thinking-exp" in model_ids
        assert "gemini-1.5-pro" in model_ids
        assert "gemini-1.5-flash" in model_ids

        # DeepSeek Provider models
        assert "deepseek-chat" in model_ids
        assert "deepseek-reasoner" in model_ids

        # ChatGPT Provider models
        assert "gpt-4o" in model_ids
        assert "gpt-4o-mini" in model_ids
        assert "o1-mini" in model_ids
        assert "o3-mini" in model_ids


def test_provider_toggles():
    app = create_app()
    with TestClient(app) as client:
        # Login as admin to get session
        login_res = client.post("/admin/api/login", json={"username": "admin", "password": "changeme"})
        assert login_res.status_code == 200

        # Toggle Gemini provider off
        for provider in ("gemini", "deepseek", "chatgpt"):
            res = client.post(f"/admin/api/providers/{provider}/toggle", json={"enabled": False})
            assert res.status_code == 200
            assert res.json()["enabled"] is False

        # Verify admin/api/models reflects toggle (all disabled -> empty list)
        res_models = client.get("/admin/api/models")
        assert res_models.status_code == 200
        assert len(res_models.json()) == 0


def test_unauthorized_request():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer invalid-token"})
        assert r.status_code == 401


def test_revoked_api_key_is_rejected():
    async def _test():
        from app.database import revoke_api_key, list_api_keys
        keys = await list_api_keys()
        test_key = next((k for k in keys if k["key_value"] == "sk-test"), None)
        assert test_key is not None
        await revoke_api_key(test_key["id"])

        # Validate that revoked key is rejected
        assert await validate_api_key("sk-test") is False

    asyncio.run(_test())

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
        assert r.status_code == 401


def test_gemini_parse_stream_chunks_and_whitespace():
    from app.providers.gemini.engine import _parse_stream_chunks

    # Raw response frame with wrb.fr and multi-line formatted text with spaces & indentation
    raw_payload = ')]}\'\n\n120\n[["wrb.fr",null,"[null,null,null,null,[[\\\"rc_123\\\",[\\\"def hello():\\\\n    return \\\\\\\"world\\\\\\\"\\\"]]]]\"]]'
    parsed = _parse_stream_chunks(raw_payload)
    assert parsed == 'def hello():\n    return "world"'

    # Test stripping of <FollowUp> chips
    raw_followup = ')]}\'\n\n80\n[["wrb.fr",null,"[null,null,null,null,[[\\\"rc_123\\\",[\\\"Here is your answer<FollowUp query=\\\\\\\"test\\\\\\\"/>\\\"]]]]\"]]'
    parsed_fu = _parse_stream_chunks(raw_followup)
    assert parsed_fu == "Here is your answer"


def test_deepseek_pow_solver():
    """Verify DeepSeek 23-round Keccak permutation and PoW challenge solver."""
    # 1. Test hash computation
    digest = deepseek_hash_v1(b"test_salt_123456_0")
    assert len(digest) == 32
    assert isinstance(digest, bytes)

    # 2. Test solve challenge
    challenge_data = {
        "algorithm": "DeepSeekHashV1",
        "challenge": "abc123challenge",
        "salt": "test_salt",
        "difficulty": 100,  # Low difficulty for instant test execution
        "expire_at": 1740000000,
        "signature": "sig_xyz",
        "target_path": "/api/v0/chat/completion",
    }
    solution = solve_pow_challenge(challenge_data)
    assert solution["algorithm"] == "DeepSeekHashV1"
    assert "answer" in solution
    assert isinstance(solution["answer"], int)

    # 3. Test base64 encoding
    header_val = encode_pow_response(solution)
    assert isinstance(header_val, str)
    decoded_json = json.loads(base64.b64decode(header_val.encode("ascii")).decode("utf-8"))
    assert decoded_json["algorithm"] == "DeepSeekHashV1"
    assert decoded_json["answer"] == solution["answer"]


def test_openai_chat_completions_multi_provider_mock():
    from app.providers.registry import register_provider
    app = create_app()
    with TestClient(app) as client:
        # Register mock dummy engine for gemini, deepseek, and chatgpt
        engine = DummyProvider()
        register_provider("gemini", engine)
        register_provider("deepseek", engine)
        register_provider("chatgpt", engine)

        # 1. Test DeepSeek Chat (Non-streaming)
        res_ds = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Explain AI"}],
                "stream": False,
            }
        )
        assert res_ds.status_code == 200
        data_ds = res_ds.json()
        assert "Explain AI" in data_ds["choices"][0]["message"]["content"]

        # 2. Test DeepSeek Reasoner (Streaming with reasoning_content)
        res_ds_stream = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": "Solve 2+2"}],
                "stream": True,
            }
        )
        assert res_ds_stream.status_code == 200
        assert "reasoning_content" in res_ds_stream.text
        assert "Thinking step by step" in res_ds_stream.text
        assert "[DONE]" in res_ds_stream.text

        # 3. Test ChatGPT (gpt-4o)
        res_gpt = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello GPT"}],
                "stream": False,
            }
        )
        assert res_gpt.status_code == 200
        assert "Hello GPT" in res_gpt.json()["choices"][0]["message"]["content"]


def test_multi_provider_database_accounts():
    async def _test():
        # Add accounts for gemini, deepseek, and chatgpt
        id_gem = await add_account("gemini", "Gemini 1", {"secure_1psid": "abc"})
        id_ds  = await add_account("deepseek", "DeepSeek 1", {"userToken": "tok_123"})
        id_cgp = await add_account("chatgpt", "ChatGPT 1", {"session_token": "sess_456"})

        assert id_gem > 0
        assert id_ds > 0
        assert id_cgp > 0

        # Query accounts by provider
        gem_accts = await list_accounts("gemini")
        assert any(a["id"] == id_gem for a in gem_accts)

        ds_accts = await list_accounts("deepseek")
        assert any(a["id"] == id_ds for a in ds_accts)

        cgp_accts = await list_accounts("chatgpt")
        assert any(a["id"] == id_cgp for a in cgp_accts)

    asyncio.run(_test())


def test_env_var_account_seeding(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "test_psid_999")
    monkeypatch.setenv("GEMINI_PSIDTS", "test_psidts_888")
    monkeypatch.setenv("DEEPSEEK_TOKEN", "ds_token_777")
    monkeypatch.setenv("CHATGPT_TOKEN", "cg_token_666")

    async def _test():
        await init_db()
        gem_accts = await list_accounts("gemini")
        assert any("test_psid_999" in str(a["credentials"]) for a in gem_accts)

        ds_accts = await list_accounts("deepseek")
        assert any("ds_token_777" in str(a["credentials"]) for a in ds_accts)

        cg_accts = await list_accounts("chatgpt")
        assert any("cg_token_666" in str(a["credentials"]) for a in cg_accts)

    asyncio.run(_test())
