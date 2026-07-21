# Available Computing —— 应用接入手册

> 版本：Personal V1
> 更新日期：2026-07-21
> 面向：调用统一代理的本地应用、脚本与第三方客户端
> 关联文档：[部署指南](./05-deployment.md) · [当前架构](./03-architecture.md)

Available Computing 对 Chat、Embedding 和 Image 提供 OpenAI 风格接口，并提供 Rerank 扩展接口。调用方只使用本项目创建的 `ac_` 代理 Key，不直接接触各厂商 Key。

---

## 1. 地址与鉴权

### 1.1 Base URL

| 运行方式 | 管理页面 | SDK Base URL |
|---|---|---|
| Docker | `http://localhost:8080/` | `http://localhost:8080/v1` |
| 源码开发 | `http://localhost:5173/` | `http://localhost:8002/v1` |
| HTTPS 反向代理 | `https://ai.example.com/` | `https://ai.example.com/v1` |

源码开发时 `5173` 是页面，`8002` 是后端。Vite 也会代理浏览器中的 `/v1`，但 SDK 建议直接使用 `8002`。

### 1.2 创建代理 Key

登录管理页面，进入“设置 → API 密钥”，创建名称明确的 `ac_` Key。可配置：

- 厂商白名单或黑名单。
- 每分钟和每日请求上限（RPM/RPD）。
- 默认路由偏好：延迟或能力。
- 默认最小上下文长度。

为不同应用创建不同 Key；停用某个应用时无需更换厂商凭证。代理 Key 可在本地管理页面查看和复制，因此应把管理页面也视为敏感界面。

所有代理请求使用：

```http
Authorization: Bearer ac_your_key
```

管理 JWT 也能调用代理，主要用于本机调试；它会过期，不适合长期集成。

建议把凭证放进环境变量：

```bash
export AC_BASE_URL="http://localhost:8080/v1"
export AC_API_KEY="ac_your_key"
```

不要把真实 Key 写进源代码、Markdown、日志、截图或 Git 历史。

---

## 2. 接入前自检

先验证认证、策略与路由，不消耗上游推理额度：

```bash
curl "$AC_BASE_URL/ac/self-test" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto:text"}'
```

成功响应示例：

```json
{
  "ok": true,
  "route": "auto:text",
  "selected_model": "some-model-id",
  "candidate_count": 4,
  "checked": [
    {"model": "some-model-id", "ok": true, "reason": null}
  ]
}
```

然后查看当前池状态：

```bash
curl "$AC_BASE_URL/ac/status" \
  -H "Authorization: Bearer $AC_API_KEY"
```

这两个接口会应用该 Key 的厂商与上下文策略，所以结果就是该应用实际能看到的候选范围。

---

## 3. Chat Completions

### 3.1 cURL

```bash
curl "$AC_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:fast",
    "messages": [
      {"role": "user", "content": "用三句话解释向量数据库"}
    ]
  }'
```

流式响应：

```bash
curl -N "$AC_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:text",
    "messages": [{"role":"user","content":"你好"}],
    "stream": true
  }'
```

### 3.2 Python OpenAI SDK

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["AC_BASE_URL"],
    api_key=os.environ["AC_API_KEY"],
)

stream = client.chat.completions.create(
    model="auto:text",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)

for chunk in stream:
    text = chunk.choices[0].delta.content
    if text:
        print(text, end="")
```

### 3.3 Node.js OpenAI SDK

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: process.env.AC_BASE_URL,
  apiKey: process.env.AC_API_KEY,
});

const stream = await client.chat.completions.create({
  model: 'auto:text',
  messages: [{ role: 'user', content: '你好' }],
  stream: true,
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? '');
}
```

---

## 4. 模型与自动路由

### 4.1 获取模型

默认只返回可用于 Chat 的免费、健康且未冷却模型：

```bash
curl "$AC_BASE_URL/models" \
  -H "Authorization: Bearer $AC_API_KEY"
```

能力过滤：

```bash
curl "$AC_BASE_URL/models?category=embedding" -H "Authorization: Bearer $AC_API_KEY"
curl "$AC_BASE_URL/models?category=rerank" -H "Authorization: Bearer $AC_API_KEY"
curl "$AC_BASE_URL/models?category=image" -H "Authorization: Bearer $AC_API_KEY"
curl "$AC_BASE_URL/models?category=all" -H "Authorization: Bearer $AC_API_KEY"
```

每条记录包含标准字段，并通过 `x_ac_metadata` 提供上下文长度、健康分数、延迟、验证时间、免费类型和模态信息。

### 4.2 Chat 自动路由

| 模型值 | 选择方式 |
|---|---|
| `auto:text` | 文本模型，优先健康与低延迟 |
| `auto:vision` | 支持图片理解的 Chat 模型 |
| `auto:code` | 代码模型 |
| `auto:fast` | 当前 Chat 候选中优先低延迟 |
| `auto:smart` | 当前 Chat 候选中优先参数规模/能力 |

`auto:*` 只从符合以下条件的模型中选择：明确免费、渠道有效、健康为 `healthy` 或 `slow`、不在 429 冷却、能力匹配，并满足代理 Key 和请求策略。

Embedding 与 Rerank 当前要求填写具体模型 ID；Image 支持具体模型或 `auto:image`。

### 4.3 请求级路由策略

Chat、Image 和自检请求可带 `routing_policy`：

```json
{
  "model": "auto:text",
  "messages": [{"role": "user", "content": "你好"}],
  "routing_policy": {
    "exclude": ["openrouter"],
    "min_context": 32000,
    "prefer": "capability",
    "fallback_chain": ["auto:fast"]
  }
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `exclude` | 本次请求额外排除的厂商 ID |
| `min_context` | 本次请求要求的最小上下文长度 |
| `prefer` | `latency` 或 `capability` |
| `fallback_chain` | 主路由失败后依次尝试的模型或 `auto:*` 路由 |

请求策略只能收窄代理 Key 的权限。比如 Key 只允许 `groq`，请求不能通过策略放开其他厂商；请求的最小上下文也不能低于 Key 的默认要求。

---

## 5. Embedding

先从 `GET /models?category=embedding` 取得具体模型 ID：

```bash
curl "$AC_BASE_URL/embeddings" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-embedding-model-id",
    "input": ["第一段文本", "第二段文本"]
  }'
```

Python OpenAI SDK：

```python
result = client.embeddings.create(
    model="your-embedding-model-id",
    input=["第一段文本", "第二段文本"],
)
vectors = [row.embedding for row in result.data]
```

---

## 6. Rerank

`/v1/rerank` 采用 SiliconFlow/Cohere 风格，不属于 OpenAI 标准端点：

```bash
curl "$AC_BASE_URL/rerank" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-rerank-model-id",
    "query": "什么是免费算力代理？",
    "documents": [
      "它统一管理多个厂商的免费模型。",
      "这是一段无关的天气信息。"
    ],
    "top_n": 2,
    "return_documents": true
  }'
```

调用前通过 `GET /models?category=rerank` 获取模型 ID。第三方 OpenAI SDK 没有标准 Rerank 方法，使用其底层 HTTP 客户端或普通请求库即可。

---

## 7. Image Generation

```bash
curl "$AC_BASE_URL/images/generations" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:image",
    "prompt": "一只在月球上写代码的橘猫，扁平插画",
    "n": 1,
    "response_format": "url"
  }'
```

支持的通用字段包括 `model`、`prompt`、`n`（只能为 1）、`quality`、`size`、`response_format`（当前为 `url`）、`user`、`watermark_enabled` 与 `routing_policy`。上游是否接受可选字段取决于最终选中的模型。

Python OpenAI SDK：

```python
result = client.images.generate(
    model="auto:image",
    prompt="一只在月球上写代码的橘猫，扁平插画",
    response_format="url",
)
print(result.data[0].url)
```

---

## 8. 诊断接口

### 8.1 扩展模型目录

```bash
curl "$AC_BASE_URL/ac/models?include_unavailable=true" \
  -H "Authorization: Bearer $AC_API_KEY"
```

可用查询参数：

- `category`：按能力类型过滤。
- `include_unavailable=false`：只返回当前可路由模型。

扩展目录包含厂商、免费证据、健康状态、验证方式、验证新鲜度、429 冷却、上下文和参数规模，适合监控程序使用。

### 8.2 响应头

成功或失败响应会尽可能携带：

| Header | 说明 |
|---|---|
| `X-AC-Route` | 调用方请求的路由或模型 |
| `X-AC-Selected-Model` | 最终选中的模型 |
| `X-AC-Selected-Provider` | 最终厂商 |
| `X-AC-Actual-Model` | `厂商/模型` 组合 |
| `X-AC-Fallback-Triggered` | 是否触发回退 |
| `X-AC-Attempted-Models` | 尝试过的模型列表 |
| `X-AC-Fallback-Count` | 回退次数 |
| `X-AC-Model-Verified-At` | 选中模型最近验证时间 |
| `X-AC-Retry-After` | 建议重试等待秒数 |

如果通过浏览器读取这些头，反向代理还需允许对应的 CORS expose headers。

---

## 9. 错误与重试

统一错误结构：

```json
{
  "error": {
    "message": "No available model",
    "type": "invalid_request_error",
    "code": "no_available_models",
    "retry_after": 60,
    "attempted_models": ["model-a", "model-b"]
  }
}
```

常见状态：

| HTTP | 常见含义 | 调用方处理 |
|---:|---|---|
| `401` | Key 无效、停用或 JWT 过期 | 检查凭证，不要盲目重试 |
| `404` | 模型不存在或当前无合格候选 | 刷新模型列表或改用 `auto:*` |
| `422` | 请求字段不合法 | 修正参数 |
| `429` | 本地 Key 限制、模型预算或上游限流 | 优先读取 `retry_after` / `X-AC-Retry-After` |
| `502` | 上游返回不可用响应 | 短暂退避；检查尝试列表 |
| `503` | 当前候选都忙或无可路由渠道 | 指数退避并查询 `/ac/status` |

Chat 路由已经对可重试的上游错误执行候选回退。客户端仍应设置有限次数的指数退避，并加入随机抖动；不要无上限循环，否则会同时耗尽多个免费渠道。

---

## 10. 第三方客户端

支持自定义 OpenAI Base URL 与 API Key 的客户端，一般填写：

```text
API type: OpenAI Compatible
Base URL: http://localhost:8080/v1
API Key: ac_your_key
Model: auto:text
```

若客户端会先校验模型列表，使用 `auto:text` 前先确认它允许手工输入不在 `/v1/models` 中的虚拟模型名；否则选择列表中的具体模型。

LangChain 示例：

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ["AC_BASE_URL"],
    api_key=os.environ["AC_API_KEY"],
    model="auto:text",
)

print(llm.invoke("你好").content)
```

---

## 11. 接入检查清单

- 使用的是 `ac_` 代理 Key，而不是任一厂商 Key。
- Docker 使用 `8080/v1`；源码开发 SDK 使用 `8002/v1`。
- `/ac/self-test` 和 `/ac/status` 对该 Key 返回预期候选。
- Key 的厂商范围、RPM/RPD 和最小上下文符合当前应用用途。
- Chat 使用合适的 `auto:*` 或具体模型；Embedding/Rerank 使用对应分类中的具体 ID。
- 客户端对 429/503 有有限重试，并记录 `X-AC-*` 诊断头但不记录 Authorization。
- 真实 Key 位于 Secret 管理或环境变量中，没有进入仓库和日志。
