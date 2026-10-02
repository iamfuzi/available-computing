# 更新日志 (Changelog)

所有值得注意的项目更改都将记录在此文件中。

## [未发布]

### 2026-10-02 高峰容量与路由质量批次

**数据库连接池**
- QueuePool 从默认 5+10 提至 `DB_POOL_SIZE=30` / `DB_MAX_OVERFLOW=60`（env 可配）+ SQLite busy timeout 15s——流量高峰（~150 req/min）与备份/清理任务重叠窗口出现 6 次连接池耗尽（30s 等待后 500）

**限流预算**
- 新增 per-model RPM 覆写 Setting `model_rpm:<model_id>`（优先级：Setting → 观测头/白名单 → PROXY_DEFAULT_MODEL_RPM 地板）。动机：硅基 embeddings/rerank 不发限流头，30 RPM 地板把热点重排高峰 shed 掉 16%（26 分钟 440 次 local_model_budget_exceeded）
- `deploy-host.sh` 显式 `PROXY_API_KEY_RATE_LIMIT=300`（默认 120 在高峰不够用）
- 生产 Setting：`model_rpm:BAAI/bge-reranker-v2-m3=300`、`model_rpm:BAAI/bge-m3=300`、`provider_rpm:硅基=600`

**路由评分**
- `route_score_key` 延迟信号改用近期**真实流量（passive）成功记录的中位数**，无被动历史才回退 `last_response_ms`（探测值）。动机：探测小 payload 延迟覆盖点值后，每次探测扫尾 auto:text 都偏向刚验证的重型模型（550B 模型 12s 答 5 字问题）；探测记录不再污染排序
- 成功率与延迟合并为一次查询（`recent_traffic_evidence`），原 `recent_success_rate` 保留兼容

### 2026-09-30 生产诊断与修复批次

**探测体系（三份适配器同款误杀修复）**
- OpenRouter/智谱/Groq 探测 `max_tokens=20` 使 reasoning 型免费模型 content 恒空 → 误判 empty_response；预算提至 200 且思考字段非空即视为存活（`reasoning` / `reasoning_content`）。智谱 4.5+ 系探测附带 `thinking:{type:disabled}`（探测延迟 28s→5s），探测超时默认 10s→30s 且 env 可配
- 401/403 一律归 auth_failed 导致渠道被误标 key_invalid（实为地区/客户端类型限制）→ 按错误体分类 `access_restricted`；渠道级失效须 validate_key 复核；恢复自动关闭告警
- 429 区分平台配额（error_type）与上游过载（provider_code）；流式 SSE 中途错误事件（`error`/`finish_reason:"error"`）计入健康并走 429 冷却；解析裸 `X-RateLimit-*` 头；Groq limit-requests 头单位为 RPD

**限流与探活**
- 新增 per-channel 探测日预算 `probe_daily_budget:<id>`（心跳/发现基线/down 重探共用，manual 不受限）
- 新增渠道级 TPM 防护 `provider_tpm:<id>`：非流式按 usage 精确计量、流式按 SSE 字节估算，超限本地换道
- 全渠道 RPM/探活预算按官方文档+实测校准（OR 15/6、Groq 60、智谱 30/10、硅基 300、Kilo 3/20、Agnes 10、讯飞 30）
- `flush_usage` 加 misfire 宽限，消除每分钟的调度告警噪音

**渠道接入**
- Groq 渠道上线（经主机 v2ray 代理出海，容器 HTTP(S)_PROXY + NO_PROXY 直连白名单）；白名单显式三条 gpt-oss-120b/20b、qwen3.8-27b；音频端点与安全分类器（guard 新类别）不入聊天池
- 智谱免费线对齐官方文档：新增 glm-4.5-flash（目录不返回、靠白名单入池）；glm-z1-flash 官方已不列、实测可用保留观察

**可观测性与运维**
- 新增请求级日志表 RequestLog（7 天保留）+ `GET /api/v1/pool/request-logs` 查询端点；错误路径经 `_make_ac_error` 单点埋记
- `/v1/models` 跨渠道去重（同 `:free` slug 双渠道只列最优可路由副本）
- `scripts/deploy-host.sh` 固化生产容器全部配置（含代理）+ docker 日志轮转；`backup.sh`/`check-backup.sh` 改用 python3（无 sqlite3 CLI 依赖）并输出 `~/ac-backups`
- 6 个真死模型人工裁定出池（lyria 地区锁、inkling 客户端门槛）

### 新增 (Added)
- **候选厂商详细视图功能** - 候选厂商页面新增可点击统计卡片和详细筛选功能
  - 四种筛选类型：可继续审核、OpenAI 兼容候选、准入排除、抓取来源
  - 动态候选列表筛选和平滑滚动交互
  - 性能优化：使用 useMemo 缓存筛选结果
  - 完整的 TypeScript 类型支持和无障碍属性

### 改进 (Improved)
- 候选厂商管理页面用户体验优化
- 统计卡片可交互，支持点击筛选查看不同类型的候选厂商

### 技术细节 (Technical Details)
- 新增 `DetailView` 类型定义用于管理筛选状态
- 实现 `candidateCounts` 计算逻辑和 `visibleCandidates` 筛选逻辑  
- 添加 `showDetail` 函数处理交互和平滑滚动
- 使用 React Hooks 最佳实践：useMemo、useRef、useState

---

## 版本历史

### v0.1.0 (2026-07-31) - 候选厂商管理增强
- 重构候选厂商页面UI，新增详细视图和筛选功能
- 代码变更：+201/-59 行，主要在 `frontend/src/pages/Candidates.tsx`

### 早期版本
- Personal V1 基础功能实现（统一代理、调用日志、代理 Key 策略等）
- 三层健康监控和自动回退机制
- 候选池基础功能
