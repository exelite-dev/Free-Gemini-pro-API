# OmniBridge Project Context & Rules

## Overview
OmniBridge is a high-performance, multi-provider AI proxy server that converts free web chat interfaces and official APIs into a unified, OpenAI & Anthropic compatible API server with an advanced Glassmorphism Admin Panel.

## Active Workspace & Path
- **Location**: `C:\Users\Pouiya\Desktop\OmniBridge`
- **Port**: `8080` (Admin panel at `http://localhost:8080/admin`)

## Registered Providers (5 Providers, 21 Active Models)

### 1. Google Gemini (`gemini`)
- **Engine**: `app/providers/gemini/engine.py`
- **Models**: `gemini-3-pro`, `gemini-3-flash`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-1.5-pro`
- **Credentials**: `__Secure-1PSID`, `__Secure-1PSIDTS` cookies (with automated background refresh loop).

### 2. DeepSeek (`deepseek`)
- **Engine**: `app/providers/deepseek/engine.py`
- **Models**: `deepseek-chat`, `deepseek-reasoner`, `deepseek-expert`, `deepseek-default`, `deepseek-coder`
- **Credentials**: Official API Key (`sk-...`), Email/Password auto-login, or Local Storage `userToken`.

### 3. Alibaba Qwen (`qwen`)
- **Engine**: `app/providers/qwen/engine.py`
- **Models**: `qwen-max`, `qwen-plus`, `qwen2.5-72b-instruct`, `qwen2.5-coder-32b-instruct`
- **Credentials**: Dual-mode — Free Qwen Web Chat (`chat.qwen.ai`) or Official DashScope API Keys (`sk-...`).

### 4. Microsoft Copilot (`copilot`)
- **Engine**: `app/providers/copilot/engine.py`
- **Models**: `gpt-4o`, `copilot-creative`, `copilot-precise`
- **Credentials**: Microsoft `_U` cookie from `copilot.microsoft.com`.

### 5. Zhipu GLM (`glm`)
- **Engine**: `app/providers/glm/engine.py`
- **Models**: `glm-4-flash` (Free Unlimited), `glm-4`, `glm-4-plus`, `glm-4v`
- **Credentials**: `chat.z.ai` Web Chat JWT token or `open.bigmodel.cn` free API Key.

## Project Structure
- `app/main.py`: FastAPI entrypoint, lifespan registration of providers.
- `app/config.py`: Dynamic YAML config parser (`config.yaml`), model catalog definitions.
- `app/database.py`: Async SQLite database layer (`data/omnibridge.db`) for accounts, API keys, provider states, metrics.
- `app/models.py`: Unified Pydantic schemas (OpenAI & Anthropic request/response format).
- `app/routes/`:
  - `openai.py`: `/v1/models`, `/v1/chat/completions` (dynamic model mapper).
  - `anthropic.py`: `/anthropic/v1/messages`.
  - `admin.py`: `/admin/api/*` account management, testing, and metrics routes.
- `app/admin/templates/admin.html`: Single-page Glassmorphism UI (Dashboard, Accounts, Playground, API Keys, Guides).
- `deploy/cloudflare_worker.js`: Cloudflare Worker Edge Proxy script.

## Key Developer Commands
- Run Server: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8080`
- Query Models: `curl http://localhost:8080/v1/models -H "Authorization: Bearer sk-test"`
