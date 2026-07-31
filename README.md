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
- **三层健康监控** —— 入库基线、低频心跳、事件触发复检，并用真实调用持续更新状态
- **零运维成本** —— Docker 一行部署，SQLite 本地存储，API Key 加密存储

## 功能截图

> 算力池总览：免费模型统计、健康状态分布、响应延迟排行
> 厂商管理：添加/编辑 API Key，自动发现模型
> 候选厂商：社区免费AI服务发现、筛选和管理
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
open http://localhost:8081
```

首次访问用设置的密码登录，然后添加你的 API Key 即可。

## 用 API 密钥调用

登录后在 **设置 → API 密钥** 创建一个密钥（`ac_` 开头），然后用它调用：

> 第三方应用的完整接入流程（Key 权限、命名 profile、自检、错误重试和诊断 Header）见 [应用接入手册](./docs/06-integration.md)。服务端 profile 的配置方式见 [Routing Profiles](./profiles/README.md)。

### Python（OpenAI SDK）

```python
from openai import OpenAI

client = OpenAI(
    api_key="ac_your-api-key-here",    # 在设置页创建
    base_url="http://localhost:8081/v1"
)

# 调用指定模型
response = client.chat.completions.create(
    model="glm-4-flash",
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
curl http://localhost:8081/v1/models \
  -H "Authorization: Bearer ac_your-api-key-here"

# 聊天补全
curl http://localhost:8081/v1/chat/completions \
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

第三方应用建议使用独立 Key 和 `auto:*`，先调用 `/v1/ac/self-test` 自检，再发起真实请求。若 AC 管理者给应用分配了命名 profile，请在自检和业务请求中同时传入：

```json
{
  "model": "auto:text",
  "routing_policy": {"profile": "your-profile"}
}
```

一次请求内部的有限候选切换由 AC 完成；调用方只需按 `Retry-After` 对终态 429/503 做少量重试，并记录 `X-AC-Request-ID` 用于排查。

## 核心特性

- **自动发现** —— 添加 API Key 后自动拉取模型列表，多源判定免费状态（白名单 + API 字段 + 厂商级标记）
- **严格免费准入** —— 默认排除绑卡、一次性赠金、固定日/月额度和限时试用；社区候选只进入人工审核池
- **健康感知路由** —— 自动排除不可用模型，按健康状态和响应速度排序选最优
- **API 密钥管理** —— 独立 `ac_` Key 支持厂商白/黑名单、RPM/RPD、最小上下文和路由偏好
- **实时限流采集** —— 从 API 响应头自动获取限流数据，无需手动维护
- **自动降级** —— 429 冷却、fallback chain 和请求级路由策略共同避免反复命中故障模型
- **统一能力入口** —— 支持 `/v1/models`、Chat、Embedding、Rerank、Image 和 `/v1/ac/*` 诊断接口

## 已实现的厂商适配

| 厂商 | 接入方式 | 免费判定说明 |
|------|---------|-------------|
| Groq | 自定义 Adapter | 只路由当前白名单或 API 明确确认的免费模型，不再假设全目录免费 |
| 硅基流动（SiliconFlow） | 自定义 Adapter | 以实时免费目录为最高优先级，覆盖 Chat / Embedding / Rerank |
| OpenRouter | 自定义 Adapter | 依据目录价格字段识别当前零价格模型 |
| Agnes AI | 自定义 Adapter | 仅路由经过审核并成功验证的免费模型 |
| 智谱AI（ZhiPu） | 自定义 Adapter | 仅路由经过审核并成功验证的免费能力，支持 Image |
| Mistral AI | 声明式配置 | 审核后的允许列表，不把账号目录中的其他模型视为免费 |
| Kilo Gateway | 声明式配置 | 匿名发现 `:free` 模型和 `kilo-auto/free`，无需保存上游 Key |

表格表示代码具备接入能力，不代表每个厂商都已在当前实例启用，也不承诺免费政策永久不变。新增或重新启用厂商前仍需按管理端合规记录复核官方政策。

标准 OpenAI 兼容厂商可通过 [`providers/`](./providers/) 声明式接入；特殊接口使用 [`backend/adapters/`](./backend/adapters/) 自定义适配器。

## 开发

推荐从仓库根目录一键启动。脚本会同时启动后端 `8002` 和前端 `5173`，并在退出时关闭两者：

```bash
./scripts/dev.sh
# 打开 http://localhost:5173/
```

首次运行前仍需安装依赖：

```bash
# 后端（Python 3.11+）
cd backend
pip install -r requirements.txt

# 前端（Node 22+）
cd frontend
npm install
```

本地开发使用 `http://localhost:5173/`；Docker 单容器部署默认使用 `http://localhost:8081/`。如需其他宿主机端口，在 `.env` 中设置 `AC_PORT`。

## 备份与恢复检查

运行中的 SQLite 数据库必须使用 SQLite 在线备份，不能直接复制 WAL 模式下的单个 `db.sqlite`：

```bash
# 创建权限为 600 的一致性备份；命令会输出备份路径
./scripts/backup.sh

# 在隔离临时目录中升级迁移并校验完整性，不修改生产数据库
./scripts/check-backup.sh backend/data/backups/available-computing-YYYYMMDD-HHMMSS.db
```

`backend/data/`、`secrets/` 和 `.env` 均被 Git 忽略。代理 Key 可在本地管理页查看和复制，因此管理页与数据库也属于敏感资产；不要把任何 Key 粘贴到代码、提交记录、日志或聊天中。

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
  adapters/     # 自定义 Adapter + 声明式通用 Adapter
  api/          # REST API + OpenAI 兼容代理 + API 密钥管理
  models/       # Channel / Model / ApiKey / HealthRecord / Candidate / Notification
  services/     # 发现、三层探测、候选池、通知、加密与调度
  ws/           # WebSocket 实时推送
frontend/
  src/
    pages/      # 页面（算力池、厂商管理、模型详情、API 文档、设置）
    components/ # 通用组件
    api/        # API 客户端
    hooks/      # WebSocket hook
whitelist/
  providers.yaml # 免费模型白名单
providers/
  *.yaml          # 审核后的声明式厂商配置
```

完整文档导航见 [`docs/README.md`](./docs/README.md)。

## 路线图

- **V0.1 MVP** ✅ — Key 管理 + 自动发现 + 算力池 Dashboard + Docker 部署
- **V0.5** ✅ — OpenAI 兼容代理 + API 密钥管理 + 健康感知路由 + 智能路由 + API 文档页
- **V1.0（个人版）** ✅ — 声明式厂商、三层探测、Key 级路由、Chat/Embedding/Rerank/Image、候选池与站内通知

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
- **Three-layer health monitoring** — Baseline, low-frequency heartbeat, and event-triggered rechecks backed by real calls
- **Zero ops cost** — Docker one-liner deploy, local SQLite, encrypted API key storage

## Quick Start

```bash
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

mkdir -p secrets
echo "your-secure-password" > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(32))"

docker compose up -d
# Open http://localhost:8081
```

## API Key Usage

Create an API key (`ac_` prefix) in **Settings → API Keys**, then:

> For the complete third-party integration contract—key scoping, named profiles, self-test, retries, and diagnostic headers—see the [Application Integration Guide](./docs/06-integration.md). Profile administrators should also read [Routing Profiles](./profiles/README.md).

```python
from openai import OpenAI

client = OpenAI(
    api_key="ac_your-api-key-here",
    base_url="http://localhost:8081/v1"
)

# Call a specific model
client.chat.completions.create(
    model="glm-4-flash",
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

Give each application its own key and prefer an `auto:*` route. Run `/v1/ac/self-test` before the first real request. If the AC administrator assigned a named profile, include the same `routing_policy.profile` in both self-test and business requests. AC performs bounded candidate fallback internally; callers should only retry terminal 429/503 responses a limited number of times, honor `Retry-After`, and retain `X-AC-Request-ID` for diagnostics.

## Key Features

- **Auto-discovery** — Add API key, system fetches models and determines free status via whitelist + API fields
- **Strict admission policy** — Excludes card-required, one-time-credit, fixed daily/monthly quota, and time-limited trial candidates by default
- **Health-aware routing** — Exclude unhealthy models, sort by health + response speed
- **Per-key routing policy** — Provider allow/deny lists, RPM/RPD, minimum context, and default preference
- **Unified capability endpoints** — Models, Chat, Embedding, Rerank, Image, and `/v1/ac/*` diagnostics

## Implemented Provider Integrations

| Provider | Integration | Free-model rule |
|----------|-------------|-----------------|
| Groq | Custom adapter | Only reviewed allowlisted or API-confirmed free models are routed |
| SiliconFlow | Custom adapter | Live free catalog has priority; Chat / Embedding / Rerank supported |
| OpenRouter | Custom adapter | Detects currently zero-priced models from catalog pricing |
| Agnes AI | Custom adapter | Routes only reviewed and successfully verified free models |
| ZhiPu (智谱AI) | Custom adapter | Routes reviewed and verified free capabilities, including Image |
| Mistral AI | Declarative config | Uses a reviewed allowlist instead of treating the account catalog as free |
| Kilo Gateway | Declarative config | Anonymous `:free` discovery plus `kilo-auto/free`; no upstream key stored |

This table describes implemented integrations, not enabled channels or a promise that an upstream policy will remain free. Recheck the recorded compliance evidence before adding or re-enabling a provider.

Standard OpenAI-compatible providers can use declarative config in [`providers/`](./providers/); special APIs use custom adapters in [`backend/adapters/`](./backend/adapters/).

## Development

Install dependencies once, then start both development services from the repository root:

```bash
cd backend
pip install -r requirements.txt
cd frontend
npm install

cd ..
./scripts/dev.sh
# Frontend: http://localhost:5173/
# Backend:  http://localhost:8002/
```

Docker remains the single-container deployment and is served at `http://localhost:8081/` by default. Set `AC_PORT` in `.env` to use another host port.

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python + FastAPI + SQLModel + APScheduler |
| Frontend | React + TypeScript + Tailwind CSS + Vite |
| Database | SQLite (WAL mode) |
| Security | AES-256-GCM encrypted key storage, JWT auth, PBKDF2 key derivation |
| Deploy | Docker multi-stage build, single container |

See [`docs/README.md`](./docs/README.md) for the architecture, deployment, integration, and upgrade records.

## Roadmap

- **V0.1 MVP** ✅ — Key management + auto-discovery + pool dashboard + Docker deploy
- **V0.5** ✅ — OpenAI proxy + API key management + health-aware routing + smart routing + API docs
- **V1.0 (personal)** ✅ — Declarative providers, three-layer probing, per-key routing, Chat/Embedding/Rerank/Image, candidate pool, and in-app notifications

### License

MIT
