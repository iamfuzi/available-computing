# Available Computing（可用算力）

**自托管的开源免费 AI 算力聚合平台** —— 自动发现、持续监控、聚合展示你注册的各厂商免费模型。

[English](#english) | [中文](#中文)

---

## 中文

### 它解决什么问题？

你有一堆 AI 厂商的 API Key（Groq、SiliconFlow、Gemini...），但你不知道：

- 当前哪些模型还**免费可用**
- 免费额度**还剩多少**
- 哪个模型**响应最快**
- 厂商的免费策略**什么时候变了**

Available Computing 自动帮你盯着这些事。

### 核心特性

- **自动发现** —— 添加 API Key 后自动拉取模型列表，多源判定免费状态（白名单 + API 字段 + 厂商级标记）
- **免费类型区分** —— 永久免费 / 免费配额（有日限额）/ 新用户赠送，防止无感超额
- **实时限流采集** —— 从 API 响应头自动获取限流数据，无需手动维护
- **被动优先健康探测** —— 利用真实调用结果更新状态，主动探测仅在 4 小时无调用后触发，配额保护 ≤5%/天
- **一键复制调用示例** —— cURL / Python / Node.js，自动填入你的 Key
- **Docker 一行部署** —— `docker compose up` 即可

### 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

# 2. 设置密码和 JWT 密钥
mkdir -p secrets
echo "your-secure-password" > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(32))"

# 3. 启动
docker compose up -d

# 4. 打开浏览器
open http://localhost:8080
```

首次访问用设置的密码登录，然后添加你的 API Key 即可。

### OpenAI 兼容代理

添加厂商后，可以用 OpenAI SDK 统一调用所有免费模型：

```python
from openai import OpenAI

client = OpenAI(
    api_key="你的JWT token",       # 登录后获得的 token
    base_url="http://localhost:8080/v1"
)

# 自动路由到 Gemini
client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "你好"}]
)

# 或路由到 OpenRouter 的免费模型
client.chat.completions.create(
    model="google/gemma-4-31b-it:free",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True
)
```

支持流式（SSE）和非流式，自动记录被动健康数据。

### 开发

```bash
# 后端（需要 Python 3.12+）
cd backend
pip install -r requirements.txt
export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_PASSWORD=dev
uvicorn main:app --reload --port 8000

# 前端（需要 Node 22+）
cd frontend
npm install
npm run dev    # Vite dev server，自动代理 /api → :8000
```

### 支持的厂商

| 厂商 | 状态 | 免费模型 | 说明 |
|------|------|---------|------|
| Groq | ✅ | 全部 | 永久免费 |
| SiliconFlow（硅基流动） | ✅ | 10+ | 部分模型永久免费 |
| Google Gemini | ✅ | 9 | 免费配额 |
| OpenRouter | ✅ | 29+ | 聚合平台，自动检测免费模型 |
| DeepSeek | 📋 | | 计划中 |
| Cloudflare Workers AI | 📋 | | 计划中 |

新增厂商只需实现一个 Adapter 文件，见 [`backend/adapters/`](./backend/adapters/)。

### 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + SQLModel + APScheduler |
| 前端 | React + TypeScript + Tailwind CSS + Vite |
| 数据库 | SQLite（默认）/ PostgreSQL（可选） |
| 部署 | Docker 多阶段构建，单容器 |

### 项目结构

```
backend/
  adapters/     # 厂商适配器（Groq / SiliconFlow / Gemini / OpenRouter）
  api/          # REST API + OpenAI 兼容代理
  models/       # 数据库模型
  services/     # 核心业务逻辑（发现、探测、加密、调度）
frontend/
  src/
    pages/      # 页面组件（算力池、厂商管理、模型详情、设置）
    components/ # 通用组件
    hooks/      # WebSocket hook
whitelist/
  providers.yaml # 免费模型白名单
docker/
  Dockerfile    # 多阶段构建
```

### 路线图

- **V0.1 MVP** ✅ — Key 管理 + 自动发现 + 算力池 Dashboard + Docker 部署
- **V0.5** ✅ — OpenAI 兼容代理 + 安全加固 + 更多厂商（OpenRouter）
- **V1.0** — 调用统计 + 智能路由 + 额度预测告警

### License

MIT

---

<a id="english"></a>

## English

### What problem does it solve?

You have API keys from multiple AI providers (Groq, SiliconFlow, Gemini...), but you don't know:

- Which models are currently **free to use**
- How much of your **free quota remains**
- Which model has the **fastest response time**
- When a provider **changes their free-tier policy**

Available Computing monitors all of this automatically.

### Key Features

- **Auto-discovery** — Add your API key, and the system automatically fetches model lists and determines free status via multiple signals (whitelist + API fields + provider-level flags)
- **Free type distinction** — Permanent free / Free quota (daily cap) / New user grant, preventing unexpected billing
- **Live rate-limit detection** — Automatically captures rate limit data from API response headers
- **Passive-first health probing** — Uses real call results to update status; active probing only triggers after 4+ hours of inactivity, capped at ≤5% of daily quota
- **One-click code examples** — cURL / Python / Node.js with your key auto-filled
- **Single-command deploy** — `docker compose up`

### Quick Start

```bash
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

mkdir -p secrets
echo "your-secure-password" > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(32))"

docker compose up -d
# Open http://localhost:8080
```

### OpenAI-Compatible Proxy

After adding providers, use OpenAI SDK to call all free models through a single endpoint:

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-jwt-token",
    base_url="http://localhost:8080/v1"
)

client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Hello"}]
)
```

Supports streaming (SSE) and non-streaming. Passive health data is recorded automatically.

### Development

```bash
# Backend (Python 3.12+)
cd backend
pip install -r requirements.txt
export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_PASSWORD=dev
uvicorn main:app --reload --port 8000

# Frontend (Node 22+)
cd frontend
npm install
npm run dev
```

### Supported Providers

| Provider | Status | Free Models | Notes |
|----------|--------|-------------|-------|
| Groq | ✅ | All | Permanently free |
| SiliconFlow | ✅ | 10+ | Select models permanently free |
| Google Gemini | ✅ | 9 | Free quota |
| OpenRouter | ✅ | 29+ | Aggregator, auto-detects free models |
| DeepSeek | 📋 | | Planned |
| Cloudflare Workers AI | 📋 | | Planned |

Adding a new provider = implement one Adapter file. See [`backend/adapters/`](./backend/adapters/).

### Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12 + FastAPI + SQLModel + APScheduler |
| Frontend | React + TypeScript + Tailwind CSS + Vite |
| Database | SQLite (default) / PostgreSQL (optional) |
| Deploy | Docker multi-stage build, single container |

### Roadmap

- **V0.1 MVP** ✅ — Key management + auto-discovery + pool dashboard + Docker deploy
- **V0.5** ✅ — OpenAI-compatible proxy + security hardening + more providers (OpenRouter)
- **V1.0** — Call statistics + smart routing + quota prediction & alerts

### License

MIT
