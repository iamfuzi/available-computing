# Available Computing —— MVP 任务拆解

> 版本：v0.1
> 日期：2026-05-05
> 目标：V0.1 MVP，支持 Groq / SiliconFlow / Gemini，1-2 周完成
> 关联文档：[03-architecture.md](./03-architecture.md)

---

## 整体节奏

```
第 1 天   项目脚手架 + 数据库 + 基础 API
第 2 天   Adapter 实现（Groq / SiliconFlow / Gemini）
第 3 天   免费判定逻辑 + 白名单
第 4 天   前端基础框架 + 算力池列表页
第 5 天   厂商管理页 + 添加流程（含 WS 推送）
第 6 天   模型详情页 + 调用示例
第 7 天   定时任务 + 健康探测
第 8 天   Docker 打包 + 端到端测试
第 9-10天 边角打磨 + 验收
```

---

## T1 — 项目脚手架

- [ ] **T1.1** 初始化 Python 项目结构（`backend/`，见架构文档 §3）
- [ ] **T1.2** 安装核心依赖：`fastapi uvicorn sqlmodel httpx apscheduler cryptography pyyaml`
- [ ] **T1.3** 初始化 React + TypeScript + Vite 项目（`frontend/`）
- [ ] **T1.4** 安装前端依赖：`shadcn/ui tailwindcss react-router-dom`
- [ ] **T1.5** 配置 Vite dev proxy：`/api` → `localhost:8000`

---

## T2 — 数据库与配置

- [ ] **T2.1** 实现 `database.py`：SQLModel engine，SQLite 路径从环境变量读取
- [ ] **T2.2** 创建 `Channel` / `Model` / `HealthRecord` / `Setting` 表（见架构文档 §4）
- [ ] **T2.3** 实现 `config.py`：读取 `ADMIN_PASSWORD` / `ADMIN_PASSWORD_FILE` / `DATA_DIR`
- [ ] **T2.4** 实现 `crypto.py`：AES-256-GCM 加解密，PBKDF2 密钥派生

---

## T3 — Adapter 层

- [ ] **T3.1** 实现 `adapters/base.py`：`ProviderAdapter` 抽象基类 + `ModelInfo` / `HealthInfo` dataclass
- [ ] **T3.2** 实现 `adapters/registry.py`：注册表，按 provider_id 查找 Adapter
- [ ] **T3.3** 实现 `adapters/groq.py`
  - `validate_key`：GET /openai/v1/models，检查 200
  - `list_models`：解析模型列表
  - `detect_free_from_api`：Groq 无 pricing 字段，返回 None（靠白名单兜底）
  - `health_check`：POST /openai/v1/chat/completions，`{"messages":[{"role":"user","content":"hi"}],"max_tokens":1}`
- [ ] **T3.4** 实现 `adapters/siliconflow.py`
  - `detect_free_from_api`：检查模型对象中的 `pricing` 或 `tags` 字段
- [ ] **T3.5** 实现 `adapters/gemini.py`
  - 注意：Gemini API 不完全兼容 OpenAI 格式，需单独处理 `list_models` 接口

---

## T4 — 白名单

- [ ] **T4.1** 创建 `whitelist/providers.yaml`，录入初始数据：
  - Groq：`free_strategy: all`
  - SiliconFlow：已知免费模型列表（含 `free_type`）
  - Gemini：`gemini-2.0-flash` / `gemini-1.5-flash`（含速率限制）
- [ ] **T4.2** 实现 `services/whitelist.py`：
  - 加载 YAML
  - `is_provider_all_free(provider_id)`
  - `match(provider_id, model_id)` → 返回白名单条目或 None

---

## T5 — 核心业务逻辑

- [ ] **T5.1** 实现 `services/discovery.py`
  - `determine_free(model, adapter, whitelist)`：四步判定流程（见架构文档 §6）
  - `discover_channel(channel_id)`：拉取模型列表 → 判定免费 → 写库
- [ ] **T5.2** 实现 `services/health.py`
  - `record_passive_health(model_id, response_ms, error_code)`
  - `active_probe(model, adapter)`：含 4h 冷却 + 5% 配额保护

---

## T6 — REST API

- [ ] **T6.1** `POST /api/v1/channels` — 添加厂商（同步验证 Key，异步触发探测）
- [ ] **T6.2** `GET /api/v1/channels` — 列出所有厂商（含每个的免费模型数、最后探测时间）
- [ ] **T6.3** `PATCH /api/v1/channels/{id}` — 编辑（备注、Base URL、启用/禁用）
- [ ] **T6.4** `DELETE /api/v1/channels/{id}` — 删除厂商及其所有模型
- [ ] **T6.5** `POST /api/v1/channels/{id}/probe` — 手动触发单个厂商重新探测
- [ ] **T6.6** `GET /api/v1/models` — 模型列表（支持 `?provider=&category=&free_only=&healthy_only=&q=`）
- [ ] **T6.7** `GET /api/v1/models/{id}` — 模型详情
- [ ] **T6.8** `GET /api/v1/models/{id}/health-history` — 最近 24h / 7d 健康记录
- [ ] **T6.9** `GET /api/v1/pool/summary` — 算力池总览（总厂商数、免费模型数、健康分布）
- [ ] **T6.10** `GET /api/v1/settings` / `PATCH /api/v1/settings` — 设置读写

---

## T7 — WebSocket 推送

- [ ] **T7.1** 实现 `ws/events.py`：连接管理、广播 `pool_updated` 事件
- [ ] **T7.2** 探测完成后触发 `pool_updated` 推送
- [ ] **T7.3** 前端 `useWebSocket` hook：接收事件 → 自动刷新统计数字

---

## T8 — 定时任务

- [ ] **T8.1** 实现 `services/scheduler.py`，注册以下任务：
  - 每 6 小时：对所有启用 Channel 重新 `discover_channel`
  - 每 2 小时：对超过 4h 无真实调用的模型触发 `active_probe`
  - 每天 00:00：清理 7 天前的 `health_record`
- [ ] **T8.2** 应用启动时（`lifespan`）初始化调度器

---

## T9 — 认证中间件

- [ ] **T9.1** 实现登录接口 `POST /api/v1/auth/login`，返回 JWT
- [ ] **T9.2** FastAPI 中间件：除登录接口外，所有 `/api/*` 需验证 JWT
- [ ] **T9.3** 前端：未登录 → 重定向到登录页；登录后 Token 存 localStorage

---

## T10 — 前端页面

### T10.1 算力池总览（首页 `/`）
- [ ] 顶部 4 个统计卡片（接入厂商数、免费模型数、健康数、今日调用）
- [ ] 筛选栏（类型 / 健康状态 / 搜索框）
- [ ] 模型表格（厂商、模型名、类型、上下文、状态、响应时间、操作菜单 `⋯`）
- [ ] 默认按响应时间升序
- [ ] **空状态**：无厂商时显示引导卡片（Groq / SiliconFlow / Gemini 快速入口）
- [ ] `⋯` 菜单：复制 Endpoint / 复制调用示例 / 刷新该模型

### T10.2 厂商管理页（`/channels`）
- [ ] 厂商卡片列表（Key 脱敏显示、免费模型数、最后探测时间、状态）
- [ ] 「+ 添加厂商」按钮
- [ ] 每张卡片操作：刷新 / 编辑 / 禁用 / 删除

### T10.3 添加厂商弹窗
- [ ] Step 1：厂商选择（支持 Groq / SiliconFlow / Gemini）
- [ ] Step 2：填写 Key + Base URL（可选）+ 直链获取 Key 的帮助链接
- [ ] 点击「验证并添加」→ 仅做 Key 验证（同步），成功后关闭弹窗
- [ ] 弹窗关闭后，厂商卡片立即出现并显示「探测中...」，探测完成后通过 WS 更新

### T10.4 模型详情页（`/models/:id`）
- [ ] 基本信息（厂商、类型、上下文、速率限制）
- [ ] 免费类型标签（永久免费 / 免费配额 / 新用户赠送 / 未知）+ 判定来源 tooltip
- [ ] 24h 健康状态图表（折线图，横轴时间，纵轴响应时间）
- [ ] 快速调用代码块（cURL / Python / Node.js Tab 切换）
  - **Key 位置显示脱敏值**（后 4 位），复制时展开完整 Key
- [ ] 📋 一键复制

### T10.5 设置页（`/settings`）
- [ ] 探测频率、响应阈值配置
- [ ] 修改登录密码
- [ ] Key 加密状态（只读，显示「始终开启」）
- [ ] 白名单版本显示

---

## T11 — Docker 打包

- [ ] **T11.1** 编写多阶段 `Dockerfile`：
  - Stage 1（node）：`npm run build` 生成前端 `/dist`
  - Stage 2（python slim）：安装依赖，复制 `/dist` 到 `/app/static`，后端 serve 静态文件
- [ ] **T11.2** FastAPI 挂载静态文件：`app.mount("/", StaticFiles(directory="static", html=True))`
- [ ] **T11.3** 编写 `docker-compose.yml`（含 volume / secret 配置）
- [ ] **T11.4** 验证 `docker compose up` 后浏览器访问完整可用

---

## T12 — 验收测试

按 PRD §8 逐条验收：

- [ ] 用户可在 Web 界面添加 Groq / SiliconFlow / Gemini 的 Key
- [ ] 添加后系统自动探测并展示该厂商的免费模型
- [ ] Dashboard 顶部能看到「当前可用免费模型数」统计卡片
- [ ] 模型列表能正确显示厂商、模型名、类型、状态
- [ ] 用户能搜索/筛选模型
- [ ] 用户能一键复制单个模型的调用示例（含真实 Key 脱敏）
- [ ] `docker run` 一行启动，浏览器访问即可

---

## 开放决策（开始写代码前确认）

| 问题 | 默认决策 | 备注 |
|------|---------|------|
| 前端静态文件 serve 方式 | FastAPI 直接 serve（单进程，无 Nginx） | 简单优先；高并发再加 Nginx |
| 登录 Token 有效期 | 7 天，无刷新机制 | 个人工具，不需要复杂 session 管理 |
| 数据目录 | `/app/data`，Docker volume 挂载 | |
| 健康探测并发数 | asyncio gather，同时最多 20 个 | 避免被厂商限流 |
