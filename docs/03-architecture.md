# Available Computing —— 当前系统架构

> 版本：Personal V1
> 更新日期：2026-07-21
> 状态：按当前代码记录（as-built）
> 关联文档：[部署指南](./05-deployment.md) · [集成指南](./06-integration.md) · [升级方案](./07-personal-v1-upgrade.md)

---

## 1. 系统边界

Available Computing 是供个人使用的自托管免费算力代理。它负责保存用户自己的厂商凭证、发现并验证合格的免费模型、统一路由请求，以及在厂商或模型失败时自动回退。

Personal V1 的边界如下：

- 单用户、单节点、SQLite；不建设多租户、计费或分布式控制面。
- 默认候选池只接受明确满足免费政策的模型；不以“厂商已接入”推导“目录中所有模型都免费”。
- 固定赠送额度、需要信用卡、限时试用、可能自动转付费或政策证据不足的服务不进入默认池。
- 社区候选源只生成审核队列，绝不自动安装 Adapter、创建 Channel 或进入代理路由。
- 上游 Key 仅在本机加密存储；代理 Key 与厂商 Key 分离。

---

## 2. 运行拓扑与端口

### 2.1 源码开发

```text
Browser / SDK
  ├─ http://localhost:5173  → Vite 前端
  └─ http://localhost:8002  → FastAPI 后端
                              ├─ /api/* 管理 API
                              ├─ /v1/* 统一代理
                              └─ /ws    WebSocket
```

Vite 将 `/api`、`/v1` 和 `/ws` 代理到 `127.0.0.1:8002`。统一启动入口为 `./scripts/dev.sh`。

### 2.2 Docker 单容器

```text
Browser / SDK → http://localhost:8080
                └─ FastAPI :8080
                   ├─ 托管已构建 React 静态文件
                   ├─ 管理 API / WebSocket
                   └─ OpenAI 兼容代理
```

容器把 `backend/data` 挂载到 `/app/data`，管理员密码与 JWT Secret 通过 Docker Secrets 注入。

---

## 3. 组件关系

```text
React + TypeScript
        │ REST / WebSocket
        ▼
FastAPI 管理面 ───────────────┐
  Channels / Models / Keys    │
  Candidates / Notifications │
                              ▼
FastAPI 代理面          Services
  Chat / Image          Discovery / Health
  Embedding / Rerank    Candidate Pool / Events
        │               Notifications / Cleanup
        ├──────────────┬──────┘
        ▼              ▼
Adapter Registry    APScheduler
  定制 Adapter        目录、探测、清理、同步
  声明式 Adapter
        │
        ▼
第三方模型厂商 API
```

主要目录：

| 目录 | 职责 |
|---|---|
| `frontend/src/` | 页面、状态展示、管理操作与诊断界面 |
| `backend/api/` | 管理 API、代理 API 与策略校验 |
| `backend/adapters/` | 定制 Adapter、声明式 Adapter 和注册表 |
| `backend/services/` | 发现、探测、候选池、事件复核、通知、清理与调度 |
| `backend/models/` | SQLModel 数据实体 |
| `providers/` | 通过严格 Schema 校验的声明式厂商配置 |
| `whitelist/` | 人工审定的免费模型基线 |
| `scripts/` | 开发启动、备份和恢复检查 |

---

## 4. 管理面与代理面

### 4.1 管理面

管理页面先使用管理员密码登录，后续通过 JWT 访问 `/api/v1/*`：

- `auth`：登录。
- `channels`：添加、编辑、删除和重新探测厂商渠道。
- `models` / `pool`：模型查询、健康历史和算力池摘要。
- `apikeys`：创建、修改、停用与删除代理 Key。
- `candidates`：刷新社区候选源、审核、忽略及生成 YAML 草稿。
- `notifications`：站内通知、未读计数和状态更新。
- `settings`：目录发现与探测周期。

WebSocket `/ws` 推送发现、探测和状态变化事件，前端断线后使用退避策略重连。

### 4.2 代理面

代理 Key 通过 `Authorization: Bearer <AC_API_KEY>` 访问：

| 方法 | 路径 | 能力 |
|---|---|---|
| `GET` | `/v1/models` | OpenAI 兼容模型目录 |
| `POST` | `/v1/chat/completions` | 文本/多模态聊天，支持 SSE |
| `POST` | `/v1/embeddings` | 向量生成 |
| `POST` | `/v1/rerank` | 文档重排 |
| `POST` | `/v1/images/generations` | 图像生成 |
| `GET` | `/v1/ac/models` | 带厂商、健康和免费证据的扩展目录 |
| `GET` | `/v1/ac/status` | 路由与渠道诊断 |
| `POST` | `/v1/ac/self-test` | 代理端到端自检 |

代理 Key 可限制厂商白/黑名单、RPM/RPD，并保存默认偏好和最小上下文长度。请求级策略可进一步收窄候选范围，但不能放宽 Key 自身的权限。

---

## 5. 厂商接入层

### 5.1 定制 Adapter

复杂厂商由 Python Adapter 处理，包括 Groq、SiliconFlow、OpenRouter、智谱和 Agnes。定制 Adapter 负责厂商特有鉴权、目录解析、请求格式、免费信号和能力映射。

### 5.2 声明式 Adapter

标准 OpenAI Chat Completions 厂商可通过 `providers/*.yaml` 接入。启动时会完整校验配置；非法配置、重复 ID 或覆盖定制 Adapter 都会使应用启动失败。

声明式配置支持：

- Bearer Token 或无鉴权渠道。
- 模型目录字段映射与嵌套能力字段。
- 明确允许列表或模型 ID 后缀判定。
- 模型覆盖、探测参数、注册门槛和合规复核信息。

当前声明式配置为 Mistral AI 与 Kilo Gateway。详见 [`providers/README.md`](../providers/README.md)。

---

## 6. 免费准入与发现

免费判定使用“证据优先、默认拒绝”的规则：

1. 读取厂商实时模型目录。
2. 优先采用明确的零价格字段、免费标记或专用免费模型命名。
3. 对没有可靠价格字段的厂商，仅接受人工审定白名单或声明式允许列表。
4. 应用模型下线、到期、计费异常和渠道合规状态。
5. 只有 `is_free = true`、渠道启用且未被风险状态隔离的模型才可能进入路由。

`free_type` 描述免费形态，`free_source` 记录证据来源。目录刷新不会把一次失败误判为永久下线，也不会把“能列出模型”当作“已完成真实推理验证”。

候选池从社区来源收集厂商线索，保存来源状态、证据、信用卡要求、准入结论和排除原因。候选项必须经过人工复核后才能转成正式配置。

---

## 7. 三层探测与被动反馈

系统把“目录存在”“真实可调用”和“最近调用表现”分开记录。三层主动验证触发为：

1. **入库基线**：新模型进入本地目录后安排一次最小真实请求，目录查询本身不算验证。
2. **低频心跳**：仅对长期无真实流量且配额允许的模型发送极小请求；受全局并发、渠道内间隔和探测预算保护。
3. **事件触发复核**：401/402/403/429 或疑似政策变化触发关联检查，避免一次瞬时错误改变长期状态。

此外，每次真实代理调用都会写入**被动反馈**，更新耗时、成功、429、鉴权、计费或上游错误；它不额外消耗请求，是路由最直接的健康信号。定时目录刷新负责新增、下线和元数据变化，但不等同于上述真实验证。

关键时间字段包括 `last_checked_at`、`last_real_call_at`、`last_success_at` 和 `last_verified_at`。只有成功的真实推理或受控探测会更新“已验证”时间。

对疑似免费政策变化，事件复核在限定窗口内关联多次检查；达到独立失败阈值后才持久化策略变化。429 会进入冷却，冷却到期后恢复候选资格并等待后续验证。

---

## 8. 路由与回退

代理根据请求能力、模型选择和策略建立候选列表：

```text
请求
  → 校验代理 Key 与请求策略
  → 筛选免费、启用、能力匹配且未冷却的模型
  → 按 latency / random / smart 等偏好排序
  → 调用首选渠道
  → 可重试错误时切换到下一候选
  → 写入被动健康记录并返回统一响应
```

显式模型只在兼容渠道之间回退；`auto:*` 模型允许系统按能力和偏好选择。流式 Chat 采用逐块转发，避免在服务器端缓冲完整响应。

---

## 9. 数据与安全

核心 SQLite 表：

| 实体 | 用途 |
|---|---|
| `Channel` | 厂商渠道、加密凭证、来源、合规快照和运行状态 |
| `Model` | 模型能力、免费证据、健康、验证新鲜度、冷却和参数规模 |
| `HealthRecord` | 主动/被动检查结果、HTTP 状态、错误原因和检查批次 |
| `ApiKey` | 认证哈希、供本地管理页查看的加密原值、权限与速率策略 |
| `CandidateProvider` | 待审核厂商、证据、准入结论和 YAML 草稿 |
| `CandidateSourceState` | 社区来源抓取状态和连续失败告警 |
| `Notification` | 去重的管理员通知及已读/解决状态 |
| `Setting` | 可在线调整的调度参数 |

安全措施：

- 厂商 Key 使用管理员密码派生密钥加密；代理 Key 使用哈希进行日常认证。
- JWT Secret 与管理员密码必须显式配置，可从 Secret 文件读取。
- 管理登录、代理请求和模型并发均有限制。
- 渠道删除使用数据库级级联；SQLite 使用 WAL，Alembic 管理结构迁移。
- 日志和文档不得记录明文厂商 Key、代理 Key 或用户密码。

---

## 10. 调度任务

| 任务 | 默认周期 | 目的 |
|---|---:|---|
| 厂商目录发现 | 6 小时 | 更新模型及免费证据 |
| 闲置模型探测 | 2 小时扫描 | 只探测符合闲置与预算条件的模型 |
| 候选源刷新 | 24 小时 | 更新人工审核队列，不自动接入 |
| 429 冷却恢复 | 5 分钟 | 让到期模型重新具备候选资格 |
| 健康历史清理 | 每日 | 控制本地数据库体积 |
| SiliconFlow 下线同步 | 每月 | 根据官方发布记录停用已退役模型 |

启动后会安排一次非阻塞的闲置模型扫描，避免频繁重启导致健康状态永远不刷新。

---

## 11. 已验证基线

截至 2026-07-21：

- 后端测试：269 项通过。
- 前端：lint 与生产构建通过，依赖审计无已知漏洞。
- Docker：镜像构建通过。
- 数据：备份与恢复检查通过。
- 实际链路：Chat、Embedding、Rerank、Image 四类能力均完成真实调用验证。

完整实施结论和保留范围见 [原方案实施进度](./08-original-plan-progress.md)。
