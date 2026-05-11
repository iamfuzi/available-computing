# 算力池（Available Computing）

**自托管的开源免费 AI 算力聚合平台** —— 一处添加 API Key，自动发现免费模型，OpenAI 兼容接口统一调用。

[中文](#中文) | [English](#english)

---

<a id="中文"></a>

## 它解决什么问题？

你有很多 AI 厂商的 API Key，但：

- 不知道哪些模型**当前免费可用**
- 不知道哪个模型**响应最快、最稳定**
- 每个项目都要单独配置不同厂商的 SDK
- 厂商**随时调整免费策略**，你无法及时感知

算力池帮你解决这一切：**添加 Key → 自动发现 → 健康监控 → 统一代理调用**。

## 核心价值

- **一个 Key 调所有模型** —— 通过 API 密钥（`ac_` 开头）统一调用所有厂商的免费模型，完全兼容 OpenAI SDK
- **自动选最好的模型** —— `model="auto:text"` 自动路由到当前最健康、最快的模型
- **7×24 健康监控** —— 被动优先 + 主动探测，实时感知模型可用性和响应速度
- **零运维成本** —— Docker 一行部署，SQLite 本地存储，API Key 加密存储

## 功能截图

> 算力池总览：免费模型统计、健康状态分布、响应延迟排行
> 厂商管理：添加/编辑 API Key，自动发现模型
> API 文档：一键复制调用示例（curl / Python / Node.js）
> 模型详情：健康历史、快速调用测试

## 快速开始

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

## 用 API 密钥调用

登录后在 **设置 → API 密钥** 创建一个密钥（`ac_` 开头），然后用它调用：

### Python（OpenAI SDK）

```python
from openai import OpenAI

client = OpenAI(
    api_key="ac_your-api-key-here",    # 在设置页创建
    base_url="http://localhost:8080/v1"
)

# 调用指定模型
response = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "你好"}],
    stream=True
)
for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")

# 自动路由到当前最优的文本模型
response = client.chat.completions.create(
    model="auto:text",
    messages=[{"role": "user", "content": "你好"}]
)
```

### cURL

```bash
# 列出可用模型
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer ac_your-api-key-here"

# 聊天补全
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ac_your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-72B-Instruct",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 智能路由

不知道用哪个模型？用 `auto:` 前缀自动选择：

| 路由 | 说明 |
|------|------|
| `auto:text` | 自动选择最快的文本对话模型 |
| `auto:vision` | 自动选择多模态理解模型 |
| `auto:code` | 自动选择代码生成模型 |

## 核心特性

- **自动发现** —— 添加 API Key 后自动拉取模型列表，多源判定免费状态（白名单 + API 字段 + 厂商级标记）
- **免费类型区分** —— 永久免费 / 免费配额（有日限额）/ 新用户赠送，防止无感超额
- **健康感知路由** —— 自动排除不可用模型，按健康状态和响应速度排序选最优
- **API 密钥管理** —— 创建独立的 `ac_` 密钥给不同服务使用，支持启用/停用
- **实时限流采集** —— 从 API 响应头自动获取限流数据，无需手动维护
- **被动优先探测** —— 利用真实调用结果更新状态，主动探测仅配额保护触发
- **OpenAI 兼容** —— 完整支持 `/v1/models` 和 `/v1/chat/completions`，流式/非流式
- **Gemini 流式支持** —— 自动转换 Gemini 协议为 OpenAI SSE 格式

## 支持的厂商

| 厂商 | 免费模型 | 说明 |
|------|---------|------|
| Groq | 全部 | 永久免费，极速推理 |
| 硅基流动（SiliconFlow） | 20+ | 文本 + 嵌入 + 重排 + 图像 + 视频生成 |
| Google Gemini | 9 | 免费配额，Flash 系列可用 |
| OpenRouter | 29+ | 聚合平台，自动检测免费模型 |
| 智谱AI（ZhiPu） | 9 | Flash 系列永久免费，含图像/视频生成 |

新增厂商只需实现一个 Adapter 文件，见 [`backend/adapters/`](./backend/adapters/)。

## 开发

```bash
# 后端（Python 3.11+）
cd backend
pip install -r requirements.txt
export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_PASSWORD=dev
uvicorn main:app --reload --port 8000

# 前端（Node 22+）
cd frontend
npm install
npm run dev    # Vite dev server，自动代理 /api → :8000
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python + FastAPI + SQLModel + APScheduler |
| 前端 | React + TypeScript + Tailwind CSS + Vite |
| 数据库 | SQLite（WAL 模式） |
| 安全 | AES-256-GCM 加密存储 API Key，JWT 认证，PBKDF2 密钥派生 |
| 部署 | Docker 多阶段构建，单容器 |

## 项目结构

```
backend/
  adapters/     # 厂商适配器（Groq / SiliconFlow / Gemini / OpenRouter / ZhiPu）
  api/          # REST API + OpenAI 兼容代理 + API 密钥管理
  models/       # 数据库模型（Channel / Model / ApiKey / HealthRecord）
  services/     # 核心业务（发现、探测、加密、调度、清理）
  ws/           # WebSocket 实时推送
frontend/
  src/
    pages/      # 页面（算力池、厂商管理、模型详情、API 文档、设置）
    components/ # 通用组件
    api/        # API 客户端
    hooks/      # WebSocket hook
whitelist/
  providers.yaml # 免费模型白名单
```

## 路线图

- **V0.1 MVP** ✅ — Key 管理 + 自动发现 + 算力池 Dashboard + Docker 部署
- **V0.5** ✅ — OpenAI 兼容代理 + API 密钥管理 + 健康感知路由 + 智能路由 + API 文档页
- **V1.0** — 调用统计 + 额度预测告警 + 更多厂商（Cerebras、Cloudflare Workers AI）

### License

MIT

---

<a id="english"></a>

## What problem does it solve?

You have API keys from multiple AI providers, but:

- You don't know which models are **currently free and available**
- You don't know which model has the **fastest, most stable response**
- Every project needs its own SDK configuration for each provider
- Providers **change free-tier policies** without notice

Available Computing solves all of this: **Add keys → Auto-discover → Health monitoring → Unified proxy**.

## Core Value

- **One key for all models** — Use API keys (`ac_` prefix) to call all providers' free models, fully OpenAI SDK compatible
- **Auto-select the best model** — `model="auto:text"` routes to the healthiest, fastest model available
- **24/7 health monitoring** — Passive-first + active probing, real-time awareness of model availability and speed
- **Zero ops cost** — Docker one-liner deploy, local SQLite, encrypted API key storage

## Quick Start

```bash
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

mkdir -p secrets
echo "your-secure-password" > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(32))"

docker compose up -d
# Open http://localhost:8080
```

## API Key Usage

Create an API key (`ac_` prefix) in **Settings → API Keys**, then:

```python
from openai import OpenAI

client = OpenAI(
    api_key="ac_your-api-key-here",
    base_url="http://localhost:8080/v1"
)

# Call a specific model
client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True
)

# Auto-route to the best available text model
client.chat.completions.create(
    model="auto:text",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Smart Routing

| Route | Description |
|-------|-------------|
| `auto:text` | Auto-select fastest text model |
| `auto:vision` | Auto-select multimodal model |
| `auto:code` | Auto-select code generation model |

## Key Features

- **Auto-discovery** — Add API key, system fetches models and determines free status via whitelist + API fields
- **Free type distinction** — Permanent free / Free quota / New user grant
- **Health-aware routing** — Exclude unhealthy models, sort by health + response speed
- **API key management** — Independent `ac_` keys per service, with enable/disable
- **OpenAI-compatible** — Full `/v1/models` and `/v1/chat/completions` support, streaming and non-streaming
- **Gemini streaming** — Auto-converts Gemini protocol to OpenAI SSE format

## Supported Providers

| Provider | Free Models | Notes |
|----------|-------------|-------|
| Groq | All | Permanently free, ultra-fast inference |
| SiliconFlow | 20+ | Text + embedding + rerank + image + video generation |
| Google Gemini | 9 | Free quota, Flash series available |
| OpenRouter | 29+ | Aggregator, auto-detects free models |
| ZhiPu (智谱AI) | 9 | Flash series permanently free, incl. image/video generation |

Adding a new provider = implement one Adapter file. See [`backend/adapters/`](./backend/adapters/).

## Development

```bash
# Backend (Python 3.11+)
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

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python + FastAPI + SQLModel + APScheduler |
| Frontend | React + TypeScript + Tailwind CSS + Vite |
| Database | SQLite (WAL mode) |
| Security | AES-256-GCM encrypted key storage, JWT auth, PBKDF2 key derivation |
| Deploy | Docker multi-stage build, single container |

## Roadmap

- **V0.1 MVP** ✅ — Key management + auto-discovery + pool dashboard + Docker deploy
- **V0.5** ✅ — OpenAI proxy + API key management + health-aware routing + smart routing + API docs
- **V1.0** — Call statistics + quota prediction & alerts + more providers (Cerebras, Cloudflare Workers AI)

### License

MIT
