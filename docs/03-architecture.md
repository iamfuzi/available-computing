# Available Computing —— 技术架构文档

> 版本：v0.5
> 日期：2026-05-06
> 关联文档：[01-PRD.md](./01-PRD.md) · [02-product-design.md](./02-product-design.md) · [05-deployment.md](./05-deployment.md)

---

## 1. 技术选型

| 层次 | 选型 | 理由 |
|------|------|------|
| 后端语言 | Python 3.12 | 开发速度快，厂商 SDK 生态最好 |
| Web 框架 | FastAPI | 原生异步、自动 OpenAPI 文档、SSE 流式支持好 |
| 任务调度 | APScheduler | 轻量，进程内调度，无需额外依赖 |
| 数据库 | SQLite（默认） | WAL 模式 + 零运维，单文件，个人场景足够 |
| ORM | SQLModel | FastAPI 同作者，Pydantic + SQLAlchemy 二合一，类型安全 |
| 实时推送 | WebSocket（FastAPI 内置） | 前端 dashboard 实时刷新用 |
| 前端框架 | React + TypeScript | 生态成熟，组件库丰富 |
| 前端构建 | Vite | 开发体验好，构建产物小 |
| UI 组件库 | Tailwind CSS | 无运行时依赖，样式可控 |
| 容器化 | Docker + Docker Compose | 单命令启动，前后端打入同一镜像 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Docker 容器                           │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  FastAPI 应用（直接 serve 静态文件，无 Nginx）         │  │
│  │                                                      │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │ REST API    │  │ WebSocket   │  │ OpenAI 代理  │  │   │
│  │  │ /api/v1/*   │  │ /ws/events  │  │ /v1/*       │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │   │
│  │         └────────────────┴─────────────────┘          │   │
│  │                          │                             │   │
│  │  ┌───────────────────────▼────────────────────────┐   │   │
│  │  │  Core Services                                 │   │   │
│  │  │  ├─ ChannelService    厂商接入、Key 管理         │   │   │
│  │  │  ├─ DiscoveryEngine   模型列表拉取、免费判定      │   │   │
│  │  │  ├─ HealthProber      被动 + 主动健康探测         │   │   │
│  │  │  ├─ WhitelistManager  本地白名单                 │   │   │
│  │  │  ├─ Scheduler         APScheduler 定时任务        │   │   │
│  │  │  ├─ EventBus          内部事件，触发 WS 推送       │   │   │
│  │  │  └─ RateLimitParser   限流数据自动采集            │   │   │
│  │  └───────────────────────┬────────────────────────┘   │   │
│  │                           │                             │   │
│  │  ┌────────────────────────▼────────────────────────┐   │   │
│  │  │  Adapter Registry                               │   │   │
│  │  │  ├─ GroqAdapter                                 │   │   │
│  │  │  ├─ SiliconFlowAdapter                          │   │   │
│  │  │  ├─ GeminiAdapter                               │   │   │
│  │  │  ├─ OpenRouterAdapter                           │   │   │
│  │  │  └─ ...（新厂商 = 新 Adapter）                   │   │   │
│  │  └───────────────────────┬────────────────────────┘   │   │
│  └──────────────────────────┼───────────────────────────┘   │
│                              │                              │
│  ┌───────────────────────────▼────────────────────────┐    │
│  │  Storage                                           │    │
│  │  SQLite (WAL): /app/data/db.sqlite                 │    │
│  │  Whitelist:   /app/whitelist/providers.yaml        │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
              │
              ▼  出站 HTTPS
    各厂商 API（Groq / SiliconFlow / Gemini / OpenRouter / ...）
```

---

## 3. 目录结构

```
available-computing/
├── backend/
│   ├── main.py                  # FastAPI 入口，挂载所有路由
│   ├── config.py                # 环境变量、配置读取（JWT_SECRET 必填）
│   ├── database.py              # SQLModel engine、WAL 模式、FK 约束
│   ├── models/                  # 数据库模型（SQLModel）
│   │   ├── channel.py
│   │   ├── model.py             # ON DELETE CASCADE 外键
│   │   ├── health_record.py     # ON DELETE CASCADE 外键
│   │   └── setting.py
│   ├── api/                     # FastAPI 路由
│   │   ├── auth.py              # 登录、JWT、限流
│   │   ├── channels.py          # /api/v1/channels/*
│   │   ├── models.py            # /api/v1/models/*
│   │   ├── pool.py              # /api/v1/pool/*
│   │   ├── settings.py          # /api/v1/settings（验证 + 调度器刷新）
│   │   └── proxy.py             # /v1/chat/completions OpenAI 兼容代理
│   ├── ws/
│   │   └── events.py            # WebSocket 推送（JWT 认证）
│   ├── services/
│   │   ├── discovery.py         # 模型发现、免费判定（bounded concurrency）
│   │   ├── health.py            # 健康探测（被动 + 主动，配额保护）
│   │   ├── whitelist.py         # 白名单加载、匹配
│   │   ├── scheduler.py         # APScheduler（从 DB 读取间隔，支持热更新）
│   │   ├── crypto.py            # Key 加密/解密（AES-256-GCM）
│   │   ├── rate_limit.py        # 限流 header 解析
│   │   ├── events.py            # 内部事件总线
│   │   └── cleanup.py           # 健康记录清理
│   ├── adapters/                # 厂商适配器
│   │   ├── base.py              # ProviderAdapter 抽象基类
│   │   ├── groq.py
│   │   ├── siliconflow.py
│   │   ├── gemini.py
│   │   ├── openrouter.py        # 自动从 pricing 字段检测免费
│   │   └── registry.py          # 适配器注册表
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Pool.tsx         # 算力池总览（首页）
│   │   │   ├── Channels.tsx     # 厂商管理
│   │   │   ├── ModelDetail.tsx  # 模型详情
│   │   │   ├── Settings.tsx     # 设置
│   │   │   └── Login.tsx        # 登录
│   │   ├── components/
│   │   │   ├── StatCard.tsx
│   │   │   ├── HealthBadge.tsx
│   │   │   ├── FreeTypeBadge.tsx
│   │   │   └── AddChannelModal.tsx
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts  # WS 连接、指数退避重连
│   │   └── api/
│   │       └── client.ts        # Axios 封装，JWT 拦截器
│   ├── package.json
│   └── vite.config.ts
│
├── whitelist/
│   └── providers.yaml           # 内置免费模型白名单
│
├── docker/
│   └── Dockerfile               # 多阶段构建：Node → Python
│
├── docker-compose.yml
├── .dockerignore
└── docs/
```

---

## 4. 数据库 Schema

```sql
-- 厂商接入实例
CREATE TABLE channel (
    id              TEXT PRIMARY KEY,       -- UUID
    provider_type   TEXT NOT NULL,          -- groq / siliconflow / gemini / openrouter
    name            TEXT NOT NULL,          -- 显示名称
    api_key_enc     TEXT NOT NULL,          -- AES-256-GCM 加密后的 Key
    base_url        TEXT,                   -- 可选，覆盖默认
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_probed_at  DATETIME
);

-- 发现的模型
CREATE TABLE model (
    id                  TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    display_name        TEXT,
    category            TEXT,               -- text / vision / code / embedding
    context_length      INTEGER,
    rate_limit          TEXT,               -- JSON: {rpm, tpm, rpd}
    rate_limit_source   TEXT,               -- manual / observed
    rate_limit_updated_at DATETIME,
    is_free             BOOLEAN,
    free_type           TEXT,               -- permanent / quota / grant / unknown
    free_source         TEXT,               -- provider_free / api_field / whitelist / unknown
    health_status       TEXT DEFAULT 'unknown',
    last_response_ms    INTEGER,
    last_checked_at     DATETIME,
    last_real_call_at   DATETIME,           -- 最近一次真实用户调用时间
    is_active           BOOLEAN DEFAULT TRUE
);

-- 健康历史（滚动保留 7 天）
CREATE TABLE health_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        TEXT NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    checked_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    status          TEXT NOT NULL,
    response_ms     INTEGER,
    error_code      TEXT,
    is_passive      BOOLEAN DEFAULT FALSE
);

-- 配置 KV 存储
CREATE TABLE setting (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
```

SQLite 启用 WAL 模式 + `PRAGMA foreign_keys=ON`，支持 CASCADE DELETE。

---

## 5. Adapter 接口

```python
# adapters/base.py
@dataclass
class ModelInfo:
    model_id: str
    display_name: str
    category: str
    context_length: Optional[int]
    rate_limit: Optional[dict]
    raw: dict

@dataclass
class HealthInfo:
    status: str             # healthy / slow / down
    response_ms: int
    error_code: Optional[str]
    observed_rate_limit: Optional[dict]     # 从响应头解析
    observed_remaining: Optional[dict]       # 剩余配额

class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @abstractmethod
    async def validate_key(self, key: str, base_url: str) -> None: ...

    @abstractmethod
    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]: ...

    @abstractmethod
    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]: ...

    @abstractmethod
    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo: ...
```

新增厂商只需：
1. 创建 `adapters/<provider>.py` 实现上述接口
2. 在 `adapters/registry.py` 注册
3. （可选）在 `whitelist/providers.yaml` 加免费模型数据

OpenRouter 不需要白名单——`detect_free_from_api` 直接从 `pricing` 字段判断 `prompt == "0" && completion == "0"` 即为免费。

---

## 6. 免费判定流程

```
输入: ModelInfo + provider_id + adapter + whitelist
  │
  ├─ Step 1: 厂商整体免费？
  │   whitelist.is_provider_all_free(provider_id)
  │   → 是: free_type=permanent, source=provider_free
  │
  ├─ Step 2: API 字段判定
  │   adapter.detect_free_from_api(model)
  │   → 有结果: source=api_field
  │
  ├─ Step 3: 白名单匹配
  │   whitelist.match(provider_id, model_id)
  │   → 匹配: free_type=entry.free_type, source=whitelist
  │
  └─ Step 4: 未知
      → is_free=None, free_type=unknown, source=unknown
      不主动探测，等待更多信息
```

---

## 7. 健康探测策略

### 被动路径（优先）

每次通过 OpenAI 代理发真实调用后，自动记录响应时间和状态。不消耗额外配额。

### 主动路径（兜底）

- **触发条件**：4 小时内无真实调用
- **配额保护**：每日主动探测 ≤ 日限额的 5%
- **并发控制**：信号量限制最多 20 个同时探测
- **限流数据**：解析响应头 `x-ratelimit-*`，`observed` 优先级高于 `manual`

### 定时任务

| 任务 | 默认间隔 | 说明 |
|------|---------|------|
| discover_all | 6 小时 | 重新发现所有启用厂商的模型 |
| probe_stale | 2 小时 | 主动探测 4h 无调用的模型 |
| cleanup_health | 每天 00:00 | 清理 7 天前的健康记录 |

间隔可通过 Settings 页面调整，修改后立即生效。

---

## 8. OpenAI 兼容代理

```
POST /v1/chat/completions
  ↓
JWT 认证（与 Dashboard 共用 token）
  ↓
从 DB 查找 model_id → channel → 解密 API key
  ↓
├─ OpenAI 兼容厂商（Groq / SiliconFlow / OpenRouter）
│   → 直接转发到 {base_url}/chat/completions
│
└─ Gemini
    → 转换为 Gemini :generateContent 格式
    → 响应转换回 OpenAI 格式
  ↓
├─ stream=true  → httpx AsyncClient + StreamingResponse（SSE）
└─ stream=false → 等待完整响应
  ↓
记录被动健康信号（response_ms, error_code）
  ↓
返回 OpenAI 格式响应
```

客户端使用：

```python
from openai import OpenAI

client = OpenAI(
    api_key="<jwt-token>",
    base_url="http://your-server:8080/v1"
)

# 自动路由到正确的厂商
client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Hello"}]
)
```

---

## 9. Key 安全存储

- **加密算法**：AES-256-GCM
- **密钥派生**：PBKDF2-HMAC-SHA256（480k 迭代），管理员密码 + 随机 Salt → 加密密钥
- **Salt 存储**：`setting` 表，线程安全生成（`threading.Lock`）
- **后端任务**：所有后台任务从 DB 解密，不传递明文 Key
- **Docker**：优先读 `*_FILE`（Docker Secrets），回退到环境变量

---

## 10. 安全措施

| 措施 | 实现 |
|------|------|
| JWT 强制 | 启动时检查 `JWT_SECRET`，不设默认值 |
| 登录保护 | `hmac.compare_digest()` 防时序攻击 + 10次/5分钟限流 |
| WebSocket 认证 | 连接时验证 JWT（query param） |
| API Key 隔离 | 加密存储，前端只显示后 4 位 |
| 级联删除 | 外键 `ON DELETE CASCADE`，删除厂商自动清理关联数据 |
| WAL 模式 | SQLite WAL 提升并发读写性能 |
| 重连保护 | WebSocket 指数退避（3s→30s） |

---

## 11. 非功能性约束

| 约束 | 实现 |
|------|------|
| Dashboard 首屏 < 1s | 静态资源由 FastAPI 直接 serve；API 走 SQLite 查询 |
| 单实例支持 500+ 模型 | SQLite WAL 模式；健康探测信号量(20)并发 |
| 探测超时 ≤ 10s | httpx `timeout=10` |
| 零外部依赖 | 除厂商 API，无任何云服务调用 |
| 新增厂商单文件 | Adapter 模式 + registry 注册 |
