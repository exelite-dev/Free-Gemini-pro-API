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
        assert "gemini-3.8-flash" in model_ids
        assert "gemini-3.7-flash" in model_ids
        assert "gemini-3.6-flash" in model_ids
        assert "gemini-3.1-pro" in model_ids
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


def test_argon_and_orchestrator_models():
    """Verify Gemini 4 Argon, Hydrogen, and Orchestrator models are properly registered and exposed."""
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
        assert r.status_code == 200
        model_ids = [m["id"] for m in r.json()["data"]]
        assert "gemini-4-argon" in model_ids
        assert "gemini-4-hydrogen" in model_ids
        assert "gemini-argon-orchestrator" in model_ids


def test_sync_gemini_endpoint():
    """Verify the /admin/api/sync-gemini auto-sync endpoint."""
    app = create_app()
    with TestClient(app) as client:
        # 1. Unauthorized request
        r_unauth = client.post("/admin/api/sync-gemini", json={"secure_1psid": "p", "secure_1psidts": "t"})
        assert r_unauth.status_code == 401

        # 2. Authorized request with Bearer API Key
        r_sync = client.post(
            "/admin/api/sync-gemini",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "secure_1psid": "fresh_psid_123",
                "secure_1psidts": "fresh_psidts_456",
                "label": "Test Extension Sync"
            }
        )
        assert r_sync.status_code == 200
        data = r_sync.json()
        assert data["success"] is True
        assert data["action"] in ("created", "updated")


def test_agenting_tool_calls_parsing_and_execution():
    """Verify tool call extraction, schema propagation, and multi-turn agent flow."""
    from app.routes.openai import _parse_emulated_tool_call, _extract_json_objects

    # 1. Test parsing of single tool call with nested parameters
    raw_single = 'Here is the tool call:\n```json\n{"name": "edit_file", "arguments": {"path": "main.py", "content": "print(1)"}}\n```'
    parsed_single = _parse_emulated_tool_call(raw_single)
    assert parsed_single is not None
    assert len(parsed_single) == 1
    assert parsed_single[0]["function"]["name"] == "edit_file"
    args = json.loads(parsed_single[0]["function"]["arguments"])
    assert args["path"] == "main.py"

    # 2. Test parsing of tool_calls array format
    raw_array = '```json\n{"tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "run_command", "arguments": "{\\"command\\": \\"pytest\\"}"}}]}\n```'
    parsed_array = _parse_emulated_tool_call(raw_array)
    assert parsed_array is not None
    assert parsed_array[0]["function"]["name"] == "run_command"
    assert "pytest" in parsed_array[0]["function"]["arguments"]

    # 3. Test parsing without markdown backticks (raw balanced JSON)
    raw_bare = 'I will now run the command: {"name": "read_file", "arguments": {"path": "config.yaml"}}'
    parsed_bare = _parse_emulated_tool_call(raw_bare)
    assert parsed_bare is not None
    assert parsed_bare[0]["function"]["name"] == "read_file"

    # 4. Test openai_request_to_unified with tools and tool outputs
    from app.models import ChatCompletionRequest, ChatMessage, openai_request_to_unified
    req = ChatCompletionRequest(
        model="gemini-3.8-flash",
        messages=[
            ChatMessage(role="user", content="Read the config file"),
            ChatMessage(role="assistant", content=None, tool_calls=[
                {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"config.yaml\"}"}}
            ]),
            ChatMessage(role="tool", tool_call_id="call_1", content="server: host=0.0.0.0"),
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read contents of a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}
            }
        }]
    )
    unified = openai_request_to_unified(req, "gemini")
    # Verify tools schema was injected into system message
    assert any("AVAILABLE TOOLS" in m.text for m in unified.messages)
    assert any("read_file" in m.text for m in unified.messages)
    # Verify tool output was preserved in context
    assert any("TOOL OUTPUT for call_1" in m.text for m in unified.messages)


# ── Additional Comprehensive Tests (Target: 56+ Total Tests) ─────────────────

def test_anthropic_v1_messages_endpoint():
    from app.providers.registry import register_provider
    app = create_app()
    with TestClient(app) as client:
        engine = DummyProvider()
        register_provider("gemini", engine)
        r = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-test"},
            json={
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Hello Anthropic"}],
                "stream": False
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "message"
        assert len(data["content"]) > 0
        assert "Hello Anthropic" in data["content"][0]["text"]


def test_anthropic_custom_prefix_messages_endpoint():
    from app.providers.registry import register_provider
    app = create_app()
    with TestClient(app) as client:
        engine = DummyProvider()
        register_provider("deepseek", engine)
        r = client.post(
            "/anthropic/v1/messages",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Test Prefix"}],
                "stream": False
            }
        )
        assert r.status_code == 200
        assert r.json()["type"] == "message"


def test_anthropic_streaming_sse_events():
    from app.providers.registry import register_provider
    app = create_app()
    with TestClient(app) as client:
        engine = DummyProvider()
        register_provider("gemini", engine)
        r = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-test"},
            json={
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Stream Anthropic"}],
                "stream": True
            }
        )
        assert r.status_code == 200
        text = r.text
        assert "event: message_start" in text
        assert "event: content_block_start" in text
        assert "event: content_block_delta" in text
        assert "event: message_delta" in text
        assert "event: message_stop" in text


def test_anthropic_missing_model_404():
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-test"},
            json={
                "model": "non-existent-model-xyz",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        assert r.status_code == 404


def test_anthropic_provider_disabled_503():
    from app.database import set_provider_enabled
    asyncio.run(set_provider_enabled("chatgpt", False))
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-test"},
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        assert r.status_code == 503
    asyncio.run(set_provider_enabled("chatgpt", True))


def test_sync_chatgpt_endpoint():
    app = create_app()
    with TestClient(app) as client:
        # Unauthorized
        r_unauth = client.post("/admin/api/sync-chatgpt", json={"session_token": "token123"})
        assert r_unauth.status_code == 401

        # Authorized
        r_sync = client.post(
            "/admin/api/sync-chatgpt",
            headers={"Authorization": "Bearer sk-test"},
            json={"session_token": "token_xyz_fresh", "label": "Extension ChatGPT"}
        )
        assert r_sync.status_code == 200
        assert r_sync.json()["success"] is True


def test_sync_gemini_preserves_existing_api_key():
    async def _test():
        acct_id = await add_account("gemini", "Gemini Key Account", {
            "secure_1psid": "old_psid",
            "secure_1psidts": "old_psidts",
            "api_key": "AIzaSySecretApiKey"
        })
        app = create_app()
        with TestClient(app) as client:
            r = client.post(
                "/admin/api/sync-gemini",
                headers={"Authorization": "Bearer sk-test"},
                json={
                    "secure_1psid": "new_psid_rotated",
                    "secure_1psidts": "new_psidts_rotated"
                }
            )
            assert r.status_code == 200
            accts = await list_accounts("gemini")
            target = next(a for a in accts if a["id"] == acct_id)
            assert target["credentials"]["secure_1psid"] == "new_psid_rotated"
            assert target["credentials"]["api_key"] == "AIzaSySecretApiKey"
    asyncio.run(_test())


def test_admin_auth_check_unauthenticated():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/admin/api/auth-check")
        assert r.status_code == 200
        assert r.json()["authenticated"] is False


def test_admin_provider_toggle_endpoints():
    app = create_app()
    with TestClient(app) as client:
        # Without admin session, throws 401
        r = client.post("/admin/api/providers/gemini/toggle", json={"enabled": False})
        assert r.status_code == 401


def test_admin_account_crud_unauthorized():
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/admin/api/accounts").status_code == 401
        assert client.post("/admin/api/accounts", json={"provider": "gemini", "label": "test", "credentials": {}}).status_code == 401
        assert client.delete("/admin/api/accounts/1").status_code == 401


def test_deepseek_token_unwrapping():
    from app.providers.deepseek.engine import DeepSeekEngine
    engine = DeepSeekEngine()
    # 1. Plain string
    assert engine._extract_token("token_abc") == "token_abc"
    # 2. JSON string with "value"
    json_str = '{"value": "extracted_jwt_token_123", "__version": "0"}'
    assert engine._extract_token(json_str) == "extracted_jwt_token_123"
    # 3. Dict with userToken
    assert engine._extract_token({"userToken": "my_user_token"}) == "my_user_token"
    # 4. Dict with value
    assert engine._extract_token({"value": "dict_val"}) == "dict_val"


def test_chatgpt_token_extraction():
    from app.providers.chatgpt.engine import ChatGPTEngine
    engine = ChatGPTEngine()
    # 1. Plain string
    assert engine._extract_session_token("sess_token") == "sess_token"
    # 2. Dict with session_token
    assert engine._extract_session_token({"session_token": "sess_1"}) == "sess_1"
    # 3. Dict with accessToken
    assert engine._extract_session_token({"accessToken": "acc_1"}) == "acc_1"
    # 4. Dict with token
    assert engine._extract_session_token({"token": "tok_1"}) == "tok_1"


def test_chatgpt_model_mapping():
    from app.providers.chatgpt.engine import ChatGPTEngine
    engine = ChatGPTEngine()
    assert engine._map_model("gpt-4o") == "gpt-4o"
    assert engine._map_model("gpt-4o-mini") == "gpt-4o-mini"
    assert engine._map_model("o1-mini") == "o1-mini"
    assert engine._map_model("o3-mini") == "o3-mini"
    assert engine._map_model("o1-preview") == "o1"
    assert engine._map_model("custom-auto") == "auto"


def test_gemini_model_aliases():
    from app.providers.gemini.engine import _MODEL_ALIASES
    assert _MODEL_ALIASES["argon"] == "gemini-4-argon"
    assert _MODEL_ALIASES["hydrogen"] == "gemini-4-hydrogen"
    assert _MODEL_ALIASES["orchestrator"] == "gemini-argon-orchestrator"
    assert _MODEL_ALIASES["3.8"] == "gemini-3.8-flash"
    assert _MODEL_ALIASES["thinking"] == "gemini-2.0-flash-thinking-exp"


def test_gemini_model_headers_presence():
    from app.providers.gemini.engine import _get_model_headers
    h_argon = _get_model_headers("gemini-4-argon")
    assert "x-goog-ext-525001261-jspb" in h_argon
    assert h_argon["x-goog-ext-73010989-jspb"] == "[1]"  # Extended thinking

    h_hydro = _get_model_headers("gemini-4-hydrogen")
    assert "x-goog-ext-525001261-jspb" in h_hydro

    h_orch = _get_model_headers("gemini-argon-orchestrator")
    assert h_orch["x-goog-ext-73010989-jspb"] == "[1]"


def test_gemini_cookie_header_builder():
    from app.providers.gemini.engine import _build_cookie_header
    creds1 = {"secure_1psid": "psid_val", "secure_1psidts": "sidts_val"}
    h1 = _build_cookie_header(creds1)
    assert "__Secure-1PSID=psid_val" in h1
    assert "__Secure-1PSIDTS=sidts_val" in h1

    # Alternative key names
    creds2 = {"__Secure-1PSID": "p2", "__Secure-1PSIDTS": "ts2"}
    h2 = _build_cookie_header(creds2)
    assert "__Secure-1PSID=p2" in h2
    assert "__Secure-1PSIDTS=ts2" in h2


def test_tool_call_normalization_edge_cases():
    from app.routes.openai import _normalize_single_tool_call
    # 1. Non-dict input
    assert _normalize_single_tool_call("not a dict") is None
    # 2. Missing name
    assert _normalize_single_tool_call({"arguments": "{}"}) is None
    # 3. Format B with parameters key and list arguments
    res = _normalize_single_tool_call({
        "name": "batch_run",
        "parameters": ["cmd1", "cmd2"]
    })
    assert res is not None
    assert res["function"]["name"] == "batch_run"
    assert json.loads(res["function"]["arguments"]) == ["cmd1", "cmd2"]


def test_tool_call_with_persian_arguments():
    from app.routes.openai import _parse_emulated_tool_call
    raw = '''```json
    {
        "name": "send_notification",
        "arguments": {
            "message": "سلام به دنیای هوش مصنوعی",
            "priority": "فوری"
        }
    }
    ```'''
    parsed = _parse_emulated_tool_call(raw)
    assert parsed is not None
    args = json.loads(parsed[0]["function"]["arguments"])
    assert "سلام به دنیای هوش مصنوعی" in args["message"]
    assert args["priority"] == "فوری"


def test_tool_call_malformed_json_recovery():
    from app.routes.openai import _parse_emulated_tool_call
    broken = "```json\n{ broken json without quotes: 123 \n```"
    assert _parse_emulated_tool_call(broken) is None
    assert _parse_emulated_tool_call("") is None


def test_database_busy_timeout_pragma():
    from app.database import get_db
    async def _test():
        async with get_db() as db:
            async with db.execute("PRAGMA busy_timeout;") as cur:
                row = await cur.fetchone()
                assert row[0] >= 5000
    asyncio.run(_test())


def test_database_metrics_recording_and_summary():
    from app.database import record_metric, get_metrics_summary
    async def _test():
        await record_metric("gemini", "gemini-3.8-flash", True, 150.0, 100, 200)
        await record_metric("gemini", "gemini-3.8-flash", False, 0.0)
        summary = await get_metrics_summary(since_seconds=3600)
        assert summary["total"] >= 2
        assert "providers" in summary
    asyncio.run(_test())


def test_database_mask_credentials():
    from app.database import _mask_credentials
    masked = _mask_credentials({
        "secure_1psid": "g.a000abcdef1234567890",
        "short": "1234"
    })
    assert "g.a0" in masked["secure_1psid"]
    assert "7890" in masked["secure_1psid"]
    assert "*" in masked["secure_1psid"]
    assert masked["short"] == "****"


def test_openai_chat_completions_invalid_api_key():
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-invalid-key-999"},
            json={
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        assert r.status_code == 401


def test_openai_chat_completions_unknown_model_404():
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "model-does-not-exist",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        assert r.status_code == 404


def test_openai_chat_completions_provider_disabled_503():
    from app.database import set_provider_enabled
    asyncio.run(set_provider_enabled("deepseek", False))
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
        assert r.status_code == 503
    asyncio.run(set_provider_enabled("deepseek", True))


def test_openai_models_endpoint_filtering():
    from app.database import set_provider_enabled
    asyncio.run(set_provider_enabled("chatgpt", False))
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
        assert r.status_code == 200
        model_ids = [m["id"] for m in r.json()["data"]]
        assert "gpt-4o" not in model_ids
        assert "gemini-3.8-flash" in model_ids
    asyncio.run(set_provider_enabled("chatgpt", True))


def test_rate_limit_error_exception_status():
    from app.providers.base import RateLimitError
    e = RateLimitError("Rate limit reached")
    assert e.status_code == 429
    assert e.retry is True


def test_auth_error_exception_status():
    from app.providers.base import AuthError
    e = AuthError("Invalid cookie")
    assert e.status_code == 401
    assert e.retry is True


def test_provider_error_custom_status():
    from app.providers.base import ProviderError
    e = ProviderError("Bad Gateway", status_code=502, retry=True)
    assert e.status_code == 502
    assert e.retry is True


def test_deepseek_pow_solution_bounds():
    from app.providers.deepseek.pow_solver import solve_pow_challenge
    challenge = {
        "algorithm": "DeepSeekHashV1",
        "challenge": "challenge_bound_test",
        "salt": "salt_abc",
        "difficulty": 10,
        "expire_at": 1800000000,
        "signature": "sig_test",
        "target_path": "/test"
    }
    sol = solve_pow_challenge(challenge)
    assert sol["answer"] >= 0
    assert sol["algorithm"] == "DeepSeekHashV1"


def test_deepseek_hash_vector_consistency():
    from app.providers.deepseek.pow_solver import deepseek_hash_v1
    h1 = deepseek_hash_v1(b"deterministic_input_data")
    h2 = deepseek_hash_v1(b"deterministic_input_data")
    assert h1 == h2
    assert len(h1) == 32


def test_gemini_api_payload_with_system_instruction():
    from app.providers.gemini.engine import GeminiEngine
    from app.models import UnifiedRequest, UnifiedMessage
    engine = GeminiEngine()
    req = UnifiedRequest(
        model_id="gemini-3.8-flash",
        provider="gemini",
        messages=[
            UnifiedMessage(role="system", text="System instruction text"),
            UnifiedMessage(role="user", text="User question")
        ]
    )
    payload = asyncio.run(engine._build_api_payload(req))
    assert "systemInstruction" in payload
    assert payload["systemInstruction"]["parts"][0]["text"] == "System instruction text"
    assert payload["contents"][0]["role"] == "user"


def test_gemini_api_payload_with_tools():
    from app.providers.gemini.engine import GeminiEngine
    from app.models import UnifiedRequest, UnifiedMessage
    engine = GeminiEngine()
    req = UnifiedRequest(
        model_id="gemini-3.8-flash",
        provider="gemini",
        messages=[UnifiedMessage(role="user", text="Run tool")],
        tools=[{
            "type": "function",
            "function": {
                "name": "weather_lookup",
                "description": "Look up weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}
            }
        }]
    )
    payload = asyncio.run(engine._build_api_payload(req))
    assert "tools" in payload
    assert payload["tools"][0]["function_declarations"][0]["name"] == "weather_lookup"


def test_gemini_api_payload_generation_config():
    from app.providers.gemini.engine import GeminiEngine
    from app.models import UnifiedRequest, UnifiedMessage
    engine = GeminiEngine()
    req = UnifiedRequest(
        model_id="gemini-3.8-flash",
        provider="gemini",
        messages=[UnifiedMessage(role="user", text="Hi")],
        temperature=0.7,
        max_tokens=2048
    )
    payload = asyncio.run(engine._build_api_payload(req))
    assert payload["generationConfig"]["temperature"] == 0.7
    assert payload["generationConfig"]["maxOutputTokens"] == 2048


def test_google_proxy_requires_api_key():
    from unittest.mock import patch, AsyncMock
    import httpx
    app = create_app()
    with TestClient(app) as client:
        fake_response = httpx.Response(
            status_code=400,
            json={"error": {"code": 400, "message": "API key not valid"}},
            headers={"content-type": "application/json"}
        )
        with patch.object(httpx.AsyncClient, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = fake_response
            r = client.post(
                "/google/v1beta/models/gemini-pro:generateContent",
                json={"contents": [{"parts": [{"text": "Hello"}]}]}
            )
            assert r.status_code == 400
            assert "error" in r.json()


def test_google_proxy_with_valid_key_and_mock():
    from unittest.mock import patch, AsyncMock
    import httpx
    app = create_app()
    with TestClient(app) as client:
        fake_response = httpx.Response(
            status_code=200,
            json={"candidates": [{"content": {"parts": [{"text": "Hello Google Proxy"}]}}]},
            headers={"content-type": "application/json"}
        )
        with patch.object(httpx.AsyncClient, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = fake_response
            r = client.post(
                "/google/v1beta/models/gemini-2.5-flash:generateContent?key=AIzaTestKey",
                json={"contents": [{"parts": [{"text": "Hello Google Proxy"}]}]}
            )
            assert r.status_code == 200
            assert "candidates" in r.json()
            called_req = mock_send.call_args[0][0]
            assert "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" in str(called_req.url)
            assert "key=AIzaTestKey" in str(called_req.url)


def test_account_fail_count_increments():
    from app.database import record_account_use
    async def _test():
        acct_id = await add_account("gemini", "Fail Test Acct", {"fail": True})
        await record_account_use(acct_id, failed=True)
        accts = await list_accounts("gemini")
        target = next(a for a in accts if a["id"] == acct_id)
        assert target["fail_count"] >= 1
    asyncio.run(_test())


def test_account_toggle_disables_usage():
    from app.database import set_account_enabled
    async def _test():
        acct_id = await add_account("deepseek", "Toggle Acct", {"token": "t"})
        await set_account_enabled(acct_id, False)
        accts = await list_accounts("deepseek")
        target = next(a for a in accts if a["id"] == acct_id)
        assert target["enabled"] == 0
    asyncio.run(_test())


def test_delete_account_removes_from_db():
    from app.database import delete_account
    async def _test():
        acct_id = await add_account("chatgpt", "To Delete", {"token": "d"})
        await delete_account(acct_id)
        accts = await list_accounts("chatgpt")
        assert not any(a["id"] == acct_id for a in accts)
    asyncio.run(_test())


def test_update_account_label_and_credentials():
    from app.database import update_account
    async def _test():
        acct_id = await add_account("gemini", "Initial Label", {"secure_1psid": "p1"})
        await update_account(acct_id, "Updated Label", {"secure_1psid": "p2"})
        accts = await list_accounts("gemini")
        target = next(a for a in accts if a["id"] == acct_id)
        assert target["label"] == "Updated Label"
        assert target["credentials"]["secure_1psid"] == "p2"
    asyncio.run(_test())


def test_list_accounts_filter_by_provider():
    async def _test():
        await add_account("gemini", "G1", {"p": 1})
        await add_account("deepseek", "D1", {"p": 2})
        gem_only = await list_accounts("gemini")
        assert all(a["provider"] == "gemini" for a in gem_only)
    asyncio.run(_test())


def test_openai_chat_completions_with_tools_emulated_flow():
    from app.providers.registry import register_provider
    class ToolYieldingProvider(AbstractProvider):
        provider_name = "gemini"
        async def chat(self, request, account_id, credentials):
            return '```json\n{"tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}]}\n```'
        async def stream_chat(self, request, account_id, credentials):
            yield '```json\n{"tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}]}\n```'
        async def validate(self, credentials):
            return True

    app = create_app()
    with TestClient(app) as client:
        register_provider("gemini", ToolYieldingProvider())
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
                "tools": [{
                    "type": "function",
                    "function": {"name": "calculator", "parameters": {"expr": {"type": "string"}}}
                }],
                "stream": False
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert data["choices"][0]["finish_reason"] == "tool_calls"
        calls = data["choices"][0]["message"]["tool_calls"]
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "calculator"
        assert "2+2" in calls[0]["function"]["arguments"]


def test_openai_streaming_tool_calls_interception():
    from app.providers.registry import register_provider
    class ToolStreamProvider(AbstractProvider):
        provider_name = "gemini"
        async def chat(self, request, account_id, credentials):
            return '{"name": "fetch_data", "arguments": {"id": 42}}'
        async def stream_chat(self, request, account_id, credentials):
            yield '{"name": "fetch_data", "arguments": {"id": 42}}'
        async def validate(self, credentials):
            return True

    app = create_app()
    with TestClient(app) as client:
        register_provider("gemini", ToolStreamProvider())
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Fetch 42"}],
                "tools": [{
                    "type": "function",
                    "function": {"name": "fetch_data", "parameters": {"id": {"type": "integer"}}}
                }],
                "stream": True
            }
        )
        assert r.status_code == 200
        text = r.text
        assert "tool_calls" in text
        assert "fetch_data" in text
        assert "[DONE]" in text


# ── Anti-Ban & Subscription Shield Tests ─────────────────────────────────────

def test_config_anti_ban_defaults():
    from app.config import settings
    assert settings.anti_ban_shield is True
    assert settings.min_request_jitter >= 0
    assert settings.max_request_jitter >= settings.min_request_jitter


def test_gemini_client_hints_and_modern_headers():
    from app.providers.gemini.engine import _CLIENT_HINTS_HEADERS, _CHROME_VERSION, _USER_AGENT
    assert _CHROME_VERSION == "131"
    assert "131" in _USER_AGENT
    assert "Chromium" in _CLIENT_HINTS_HEADERS["sec-ch-ua"]
    assert _CLIENT_HINTS_HEADERS["sec-ch-ua-mobile"] == "?0"
    assert _CLIENT_HINTS_HEADERS["sec-ch-ua-platform"] == '"Windows"'
    assert _CLIENT_HINTS_HEADERS["sec-fetch-dest"] == "empty"
    assert _CLIENT_HINTS_HEADERS["sec-fetch-mode"] == "cors"
    assert _CLIENT_HINTS_HEADERS["sec-fetch-site"] == "same-origin"


def test_gemini_session_cache_avoids_repeated_init():
    from app.providers.gemini.engine import GeminiEngine, _SESSION_CACHE
    from unittest.mock import AsyncMock, patch, MagicMock

    engine = GeminiEngine()
    fake_html = 'window.WIZ_global_data = {"SNlM0e":"test_at_token_abc","cfb2h":"test_bl_token","FdrFJe":"test_session_id"};'
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html
    mock_resp.cookies = {}

    session = MagicMock()
    session.get = AsyncMock(return_value=mock_resp)

    _SESSION_CACHE.clear()
    account_id = 999
    creds = {"secure_1psid": "fake_psid"}

    async def _run():
        # First call: hits session.get and caches
        at1, bl1, sid1 = await engine._get_session_tokens(session, creds, account_id=account_id)
        assert at1 == "test_at_token_abc"
        assert session.get.call_count == 1
        assert account_id in _SESSION_CACHE

        # Second call: served directly from _SESSION_CACHE without network request!
        at2, bl2, sid2 = await engine._get_session_tokens(session, creds, account_id=account_id)
        assert at2 == "test_at_token_abc"
        assert session.get.call_count == 1  # Still 1! Zero extra HTTP requests

        # Force refresh: forces network request
        at3, bl3, sid3 = await engine._get_session_tokens(session, creds, account_id=account_id, force_refresh=True)
        assert session.get.call_count == 2  # Incremented to 2

    asyncio.run(_run())
    _SESSION_CACHE.clear()


def test_pool_account_concurrency_lock():
    from app.providers.pool import AccountPool
    class SlowProvider(AbstractProvider):
        provider_name = "test_provider"
        def __init__(self):
            self.running = 0
            self.max_concurrent = 0

        async def chat(self, request, account_id, credentials):
            self.running += 1
            if self.running > self.max_concurrent:
                self.max_concurrent = self.running
            await asyncio.sleep(0.05)
            self.running -= 1
            return "ok"

        async def stream_chat(self, request, account_id, credentials):
            yield "ok"

        async def validate(self, credentials):
            return True

    provider = SlowProvider()
    pool = AccountPool("test_provider", provider)

    async def _run():
        req = UnifiedRequest(model_id="test", provider="test_provider", messages=[])
        # Two simultaneous tasks for account #1
        task1 = asyncio.create_task(pool.chat(req))
        task2 = asyncio.create_task(pool.chat(req))
        await asyncio.gather(task1, task2)

        # Max concurrent executions on the same account must be exactly 1!
        assert provider.max_concurrent == 1

    asyncio.run(_run())


def test_cookie_refresh_skips_recently_active_account():
    import time
    from app.providers.gemini.cookie_refresh import _refresh_single_account
    from unittest.mock import AsyncMock, patch

    recent_account = {
        "id": 888,
        "label": "Recent Account",
        "credentials": {"secure_1psid": "p"},
        "last_used": time.time() - 300,  # 5 minutes ago
    }

    async def _run():
        with patch("app.providers.gemini.engine.GeminiEngine._get_session_tokens", new_callable=AsyncMock) as mock_get:
            await _refresh_single_account(recent_account)
            # Must NOT call _get_session_tokens because account was active recently!
            assert mock_get.call_count == 0

    asyncio.run(_run())





