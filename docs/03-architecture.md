# Available Computing —— 技术架构文档

> 版本：v0.1 草案
> 日期：2026-05-05
> 关联文档：[01-PRD.md](./01-PRD.md) · [02-product-design.md](./02-product-design.md)

---

## 1. 技术选型

| 层次 | 选型 | 理由 |
|------|------|------|
| 后端语言 | Python 3.12 | 开发速度快，厂商 SDK 生态最好 |
| Web 框架 | FastAPI | 原生异步、自动 OpenAPI 文档、SSE 流式支持好 |
| 任务调度 | APScheduler | 轻量，进程内调度，无需额外依赖 |
| 数据库 | SQLite（默认）/ PostgreSQL（可选） | SQLite 零运维，单文件，够用于个人场景 |
| ORM | SQLModel | FastAPI 同作者，Pydantic + SQLAlchemy 二合一，类型安全 |
| 实时推送 | WebSocket（FastAPI 内置） | 前端 dashboard 实时刷新用 |
| 前端框架 | React + TypeScript | 生态成熟，组件库丰富 |
| 前端构建 | Vite | 开发体验好，构建产物小 |
| UI 组件库 | shadcn/ui + Tailwind CSS | 无运行时依赖，样式可控 |
| 容器化 | Docker + Docker Compose | 单命令启动，前后端打入同一镜像 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Docker 容器                           │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Nginx（静态文件 + 反向代理）                          │  │
│  │   /           → React 前端（静态）                    │  │
│  │   /api/*      → FastAPI 后端                          │  │
│  │   /ws/*       → WebSocket                             │  │
│  └───────────────────────────┬──────────────────────────┘  │
│                               │                             │
│  ┌────────────────────────────▼────────────────────────┐   │
│  │  FastAPI 应用                                        │   │
│  │                                                      │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │ REST API    │  │ WebSocket   │  │ OpenAI 代理  │  │   │
│  │  │ /api/v1/*   │  │ /ws/events  │  │ /v1/*  V1+  │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │   │
│  │         └────────────────┴─────────────────┘          │   │
│  │                          │                             │   │
│  │  ┌───────────────────────▼────────────────────────┐   │   │
│  │  │  Core Services                                 │   │   │
│  │  │  ├─ ChannelService    厂商接入、Key 管理         │   │   │
│  │  │  ├─ DiscoveryEngine   模型列表拉取、免费判定      │   │   │
│  │  │  ├─ HealthProber      被动 + 主动健康探测         │   │   │
│  │  │  ├─ WhitelistManager  本地 + 远程白名单           │   │   │
│  │  │  ├─ Scheduler         APScheduler 定时任务        │   │   │
│  │  │  └─ EventBus          内部事件，触发 WS 推送       │   │   │
│  │  └───────────────────────┬────────────────────────┘   │   │
│  │                           │                             │   │
│  │  ┌────────────────────────▼────────────────────────┐   │   │
│  │  │  Adapter Registry                               │   │   │
│  │  │  ├─ GroqAdapter                                 │   │   │
│  │  │  ├─ SiliconFlowAdapter                          │   │   │
│  │  │  ├─ GeminiAdapter                               │   │   │
│  │  │  └─ ...（新厂商 = 新 Adapter）                   │   │   │
│  │  └───────────────────────┬────────────────────────┘   │   │
│  └──────────────────────────┼───────────────────────────┘   │
│                              │                              │
│  ┌───────────────────────────▼────────────────────────┐    │
│  │  Storage                                           │    │
│  │  SQLite: /app/data/db.sqlite                       │    │
│  │  Whitelist: /app/data/whitelist.yaml               │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
              │
              ▼  出站 HTTPS
    各厂商 API（Groq / SiliconFlow / Gemini / ...）
```

---

## 3. 目录结构

```
available-computing/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 环境变量、配置读取
│   ├── database.py              # SQLModel engine、session
│   ├── models/                  # 数据库模型（SQLModel）
│   │   ├── channel.py
│   │   ├── model.py
│   │   └── health_record.py
│   ├── schemas/                 # Pydantic 请求/响应结构
│   │   ├── channel.py
│   │   └── model.py
│   ├── api/                     # FastAPI 路由
│   │   ├── channels.py          # /api/v1/channels/*
│   │   ├── models.py            # /api/v1/models/*
│   │   ├── pool.py              # /api/v1/pool/*（算力池总览）
│   │   ├── settings.py          # /api/v1/settings/*
│   │   └── proxy.py             # /v1/* OpenAI 兼容代理（V1.0+）
│   ├── ws/
│   │   └── events.py            # WebSocket 推送
│   ├── services/
│   │   ├── discovery.py         # 模型发现、免费判定
│   │   ├── health.py            # 健康探测（被动 + 主动）
│   │   ├── whitelist.py         # 白名单加载、匹配
│   │   ├── scheduler.py         # APScheduler 任务注册
│   │   └── crypto.py            # Key 加密/解密（AES-GCM）
│   ├── adapters/                # 厂商适配器
│   │   ├── base.py              # ProviderAdapter 抽象基类
│   │   ├── groq.py
│   │   ├── siliconflow.py
│   │   ├── gemini.py
│   │   └── registry.py          # 适配器注册表
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Pool.tsx         # 算力池总览（首页）
│   │   │   ├── Channels.tsx     # 厂商管理
│   │   │   ├── ModelDetail.tsx  # 模型详情
│   │   │   └── Settings.tsx     # 设置
│   │   ├── components/
│   │   │   ├── ModelTable.tsx
│   │   │   ├── StatCard.tsx
│   │   │   ├── HealthBadge.tsx
│   │   │   └── AddChannelModal.tsx
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts  # WS 连接、自动重连
│   │   └── api/
│   │       └── client.ts        # fetch 封装
│   ├── package.json
│   └── vite.config.ts
│
├── whitelist/
│   └── providers.yaml           # 内置免费模型白名单
│
├── docker/
│   ├── Dockerfile               # 多阶段构建：前端 → 后端镜像
│   └── nginx.conf
│
├── docker-compose.yml
└── docs/
```

---

## 4. 数据库 Schema

```sql
-- 厂商接入实例
CREATE TABLE channel (
    id              TEXT PRIMARY KEY,       -- UUID
    provider_type   TEXT NOT NULL,          -- groq / siliconflow / gemini
    name            TEXT NOT NULL,          -- 用户备注
    api_key_enc     TEXT NOT NULL,          -- AES-GCM 加密后的 Key
    base_url        TEXT,                   -- 可选，覆盖默认
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_probed_at  DATETIME
);

-- 发现的模型
CREATE TABLE model (
    id              TEXT PRIMARY KEY,       -- UUID
    channel_id      TEXT NOT NULL REFERENCES channel(id),
    model_id        TEXT NOT NULL,          -- 厂商原始 model ID
    display_name    TEXT,
    category        TEXT,                   -- text / vision / code / embedding
    context_length  INTEGER,
    rate_limit      TEXT,                   -- JSON: {rpm, tpm, rpd}
    is_free         BOOLEAN,
    free_type       TEXT,                   -- permanent / quota / grant / unknown
    free_source     TEXT,                   -- provider_free / api_field / whitelist / unknown
    health_status   TEXT DEFAULT 'unknown', -- healthy / slow / down / unknown
    last_response_ms INTEGER,
    last_checked_at DATETIME,
    last_real_call_at DATETIME,             -- 最近一次真实用户调用时间（被动健康信号）
    is_active       BOOLEAN DEFAULT TRUE,   -- 厂商是否还提供此模型
    UNIQUE(channel_id, model_id)
);

-- 健康历史（滚动保留 7 天）
CREATE TABLE health_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        TEXT NOT NULL REFERENCES model(id),
    checked_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    status          TEXT NOT NULL,
    response_ms     INTEGER,
    error_code      TEXT,                   -- rate_limited / auth_failed / timeout 等
    is_passive      BOOLEAN DEFAULT FALSE   -- true=来自真实用户调用，false=主动探测
);

-- 配置 KV 存储（探测频率、阈值等）
CREATE TABLE setting (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
```

---

## 5. Adapter 接口（Python）

```python
# adapters/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class ModelInfo:
    model_id: str
    display_name: str
    category: str           # text / vision / code / embedding
    context_length: Optional[int]
    rate_limit: Optional[dict]
    raw: dict               # 厂商原始响应，保留备查

@dataclass
class HealthInfo:
    status: str             # healthy / slow / down
    response_ms: int
    error_code: Optional[str]

class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...      # "groq"

    @property
    @abstractmethod
    def display_name(self) -> str: ...     # "Groq"

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @abstractmethod
    async def validate_key(self, key: str, base_url: str) -> None:
        """抛出异常即为验证失败"""

    @abstractmethod
    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]:
        """返回该厂商当前所有模型"""

    @abstractmethod
    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        """
        从 API 响应字段判断是否免费。
        返回 {"is_free": True, "free_type": "permanent"} 或 None（无法判断）
        """

    @abstractmethod
    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        """发最小探测请求，返回健康状态"""
```

新增厂商只需：
1. 创建 `adapters/<provider>.py` 实现上述接口
2. 在 `adapters/registry.py` 注册
3. 在 `whitelist/providers.yaml` 加一节免费模型数据

---

## 6. 免费判定流程（代码层）

```python
# services/discovery.py（伪代码）
async def determine_free(model: ModelInfo, adapter: ProviderAdapter, whitelist: Whitelist) -> dict:
    # Step 1: 厂商整体免费标记
    if whitelist.is_provider_all_free(adapter.provider_id):
        return {"is_free": True, "free_type": "permanent", "source": "provider_free"}

    # Step 2: API 字段
    result = adapter.detect_free_from_api(model)
    if result:
        return {**result, "source": "api_field"}

    # Step 3: 白名单匹配
    entry = whitelist.match(adapter.provider_id, model.model_id)
    if entry:
        return {"is_free": True, "free_type": entry.free_type, "source": "whitelist"}

    # Step 4: 兜底，不探测
    return {"is_free": None, "free_type": "unknown", "source": "unknown"}
```

---

## 7. 健康探测策略（代码层）

```python
# services/health.py（伪代码）

# 被动路径：每次真实调用结束后调用
async def record_passive_health(model_id: str, response_ms: int, error_code: Optional[str]):
    status = classify_status(response_ms, error_code)
    await db.insert_health_record(model_id, status, response_ms, error_code, is_passive=True)
    await db.update_model_health(model_id, status, response_ms, last_real_call_at=now())

# 主动探测：调度器调用
async def active_probe(model: Model, adapter: ProviderAdapter):
    # 4 小时内有真实调用则跳过
    if model.last_real_call_at and (now() - model.last_real_call_at) < timedelta(hours=4):
        return

    # 配额保护：当日主动探测次数 > 5% 日限额则跳过
    if await probe_count_today(model.id) > model.daily_limit * 0.05:
        return

    health = await adapter.health_check(model.model_id, key, base_url)
    await db.insert_health_record(model.id, health.status, health.response_ms,
                                  health.error_code, is_passive=False)
    await db.update_model_health(model.id, health.status, health.response_ms)
```

---

## 8. OpenAI 兼容代理（V1.0+）

```
POST /v1/chat/completions
  ↓
解析 Authorization: Bearer <local_key>
  ↓
验证 local_key 有效
  ↓
解析 model 字段：
  ├─ "available/free-text-large" → 别名路由，从算力池选最优模型
  └─ "groq/llama-3.3-70b"       → 直接路由到指定厂商
  ↓
选 Channel（轮询 / 响应时间最优）
  ↓
转发请求到厂商（替换 Authorization header）
  ├─ stream=true  → httpx AsyncClient，chunk-by-chunk 透传 SSE
  └─ stream=false → 等待完整响应后返回
  ↓
记录调用日志 + 触发被动健康信号
  ↓
返回响应给调用方（行为与 OpenAI API 完全一致）
```

关键实现细节：
- 使用 `httpx.AsyncClient` 做出站请求，天然支持流式
- `StreamingResponse` 返回 SSE，不缓冲
- 超时 / 5xx 时自动换下一个 Channel 重试（最多 2 次）

---

## 9. Key 安全存储

- 加密算法：AES-256-GCM
- 密钥派生：PBKDF2-HMAC-SHA256，用户登录密码 + 随机 Salt → 加密密钥
- Salt 存储于 `setting` 表
- 加密密钥**不持久化**，仅在内存中（服务重启需重新用密码派生）
- Docker 部署：优先读 `ADMIN_PASSWORD_FILE`（Docker Secret），回退到 `ADMIN_PASSWORD` 环境变量

---

## 10. 部署

### 单容器（推荐）

多阶段 Dockerfile：
```
Stage 1: node → 构建 React 前端 → 生成 /dist
Stage 2: python → 安装后端依赖
          复制 /dist 到 /app/static
          Nginx 同进程启动（supervisord）或后端直接 serve 静态文件
```

```yaml
# docker-compose.yml
services:
  app:
    image: available-computing:latest
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    environment:
      - ADMIN_PASSWORD_FILE=/run/secrets/ac_admin_password
    secrets:
      - ac_admin_password

secrets:
  ac_admin_password:
    file: ./secrets/admin_password.txt
```

### 开发环境

```bash
# 后端
cd backend && uvicorn main:app --reload --port 8000

# 前端
cd frontend && npm run dev   # Vite dev server，代理 /api → :8000
```

---

## 11. 非功能性约束

| 约束 | 实现方式 |
|------|---------|
| Dashboard 首屏 < 1s | 静态资源 Nginx 缓存；API 响应走数据库查询，无外部调用 |
| 单实例支持 500+ 模型 | SQLite 完全足够；健康探测异步并发执行 |
| 探测超时 ≤ 10s | httpx 请求级 timeout=10 |
| 零外部依赖 | 除厂商 API，无任何云服务调用 |
| 新增厂商单文件 | Adapter 模式保证 |
