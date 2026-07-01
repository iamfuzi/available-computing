# Available Computing —— 接入手册

> 面向需要调用本服务的开发者 / 应用
> 版本：v1.0 · 日期：2026-06-23

本项目对外提供 **OpenAI 兼容接口**。任何支持自定义 `base_url` 的 OpenAI 客户端（官方 SDK、LangChain、LiteLLM、Cherry Studio、NextChat 等）都能直接接入，只需改 `base_url` 和 `api_key` 两处。

---

## 目录

1. [核心概念](#1-核心概念)
2. [接入准备](#2-接入准备)
3. [快速开始](#3-快速开始)
4. [智能路由：怎么选模型](#4-智能路由怎么选模型)
5. [接入第三方客户端](#5-接入第三方客户端)
6. [鉴权方式](#6-鉴权方式)
7. [接口参考](#7-接口参考)
8. [Embedding 与 Rerank](#8-embedding-与-rerank)
9. [限流与错误处理](#9-限流与错误处理)
10. [常见问题](#10-常见问题)

---

## 1. 核心概念

本项目聚合了多个 AI 厂商（OpenRouter、SiliconFlow、Groq、Gemini、ZhiPu）的**免费模型**，对外暴露统一接口。你不需要关心：

- 哪些模型现在免费、哪些可用
- 各厂商的鉴权方式、请求格式差异
- 某个模型是否限流、是否暂时不可用

系统自动探测模型健康状态，在请求时路由到当前最合适的免费模型。

**一句话总结**：把它当成一个 OpenAI API，但后端是 N 个免费厂商的聚合池。

---

## 2. 接入准备

### 2.1 获取服务地址

你需要知道本项目部署后的地址，本文用 `http://your-host:8000` 代指。接口前缀是 `/v1`，即：

```
base_url = http://your-host:8000/v1
```

> 如果通过反向代理（nginx）部署，通常是 `https://your-domain/v1`。

### 2.2 创建 API Key

在管理后台（浏览器访问 `http://your-host:5173` 或 `:8000/docs`）：

1. 用管理员密码登录
2. 进入「设置 → API 密钥」
3. 创建一个密钥，得到形如 `ac_xxxxxxxxxxxxxxxx` 的字符串

**这个 Key 只在创建时完整显示一次，请立即保存。** 它长期有效，适合写入应用配置/环境变量。

> 也可以用管理员 JWT token 调用（见[第 6 节](#6-鉴权方式)），但 JWT 有过期时间，**不建议用于服务端常驻应用**。

---

## 3. 快速开始

以下示例假设：
- 服务地址：`http://localhost:8000`
- API Key：`ac_xxxxxxxx`（替换为你自己的）

### 3.1 cURL

```bash
# 发起对话（选最聪明的模型）
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer ac_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:smart",
    "messages": [{"role": "user", "content": "用三句话解释量子纠缠"}]
  }'

# 流式输出
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer ac_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:smart",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'

# 列出可用模型
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer ac_xxxxxxxx"
```

### 3.2 Python（OpenAI SDK）

```bash
pip install openai
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="ac_xxxxxxxx",
)

# 流式对话
response = client.chat.completions.create(
    model="auto:smart",          # 详见第 4 节
    messages=[{"role": "user", "content": "你好，介绍一下你自己"}],
    stream=True,
)
for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")

# 列出可用模型
for m in client.models.list().data:
    print(m.id, m.param_size)
```

### 3.3 Node.js（OpenAI SDK）

```bash
npm install openai
```

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://localhost:8000/v1',
  apiKey: 'ac_xxxxxxxx',
});

const stream = await client.chat.completions.create({
  model: 'auto:smart',
  messages: [{ role: 'user', content: '你好' }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content || '');
}
```

---

## 4. 智能路由：怎么选模型

`model` 字段除了填具体模型 id，还支持 **auto 路由前缀**，由系统自动选模型。这是本项目的核心能力。

### 4.1 三类路由前缀

| 前缀 | 含义 | 适用场景 |
|------|------|---------|
| **`auto:smart`** | 跨所有类别，选**参数量最大**的健康模型 | 要质量、要聪明，不介意慢（如复杂推理、长文写作） |
| **`auto:fast`** | 跨所有类别，选**延迟最低**的健康模型 | 要速度、要吞吐（如简单问答、批量处理） |
| `auto:text` | 文本类，选最快 | 只要文本对话 |
| `auto:vision` | 多模态类，选最快 | 要图片理解 |
| `auto:code` | 代码类，选最快 | 要代码生成 |

### 4.2 选型建议

```
                 要质量？
              ┌─── 是 ──→ auto:smart
        起点 ─┤
              └─── 否 ──→ auto:fast  （通用首选）
```

- **默认用 `auto:fast`**：覆盖绝大多数场景，速度最快
- **任务复杂时用 `auto:smart`**：需要强推理/长上下文/代码能力时，系统会选当前最大的健康模型（可能是 72B、405B 甚至 600B 级别）
- **指定具体模型**：当你明确知道要用某个模型时，直接填 id（如 `meta-llama/llama-3.3-70b-instruct`），系统支持模糊匹配（`llama-3.3-70b` 也能匹配到）

### 4.3 排序规则

`auto:smart` 和 `auto:fast` 的排序优先级：

```
1. 健康档位（永远是第一优先级）
   healthy > slow > unknown > down（down 不参与）
2. smart：参数量降序（参数量大优先，未知排最后）
   fast ：响应延迟升序（快的优先）
```

> 注意：**健康是硬门槛**。一个 healthy 的 7B 模型会优先于 slow 的 72B——系统不会把请求打到已知不健康的模型上，即使它更大。

### 4.4 查看模型参数量

调用 `GET /v1/models`，每个模型带 `param_size` 字段（单位 B，即十亿参数）：

```json
{
  "id": "meta-llama/llama-3.3-70b-instruct",
  "param_size": 70.0,
  ...
}
```

`param_size` 为 `null` 表示参数量无法自动识别（通常是闭源模型，如 gpt-4o、claude），在 smart 排序中排最后。

---

## 5. 接入第三方客户端

因为完全 OpenAI 兼容，任何支持自定义 `base_url` 的客户端都能接入。

### 5.1 LangChain（Python）

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="ac_xxxxxxxx",
    model="auto:smart",
)
print(llm.invoke("解释一下递归").content)
```

### 5.2 LiteLLM

```python
import litellm

response = litellm.completion(
    model="openai/auto:smart",                    # openai/ 前缀 + 你的模型名
    messages=[{"role": "user", "content": "你好"}],
    api_base="http://localhost:8000/v1",
    api_key="ac_xxxxxxxx",
)
print(response.choices[0].message.content)
```

### 5.3 桌面客户端（Cherry Studio / NextChat / LobeChat 等）

这类客户端通常有「自定义服务商」或「OpenAI 兼容」选项：

| 配置项 | 填写 |
|--------|------|
| API 地址 / Base URL | `http://your-host:8000/v1` |
| API Key | `ac_xxxxxxxx` |
| 模型名 | `auto:smart` 或 `auto:fast`，或从 `/v1/models` 选具体 id |

> 配置时如果客户端要求「模型列表」，可手动填入 `auto:smart`，客户端会原样转发给本项目。

### 5.4 curl 作为通用兜底

不支持 SDK 的环境（CI 脚本、shell 工具），直接用 curl 调第 3.1 节的命令即可。

---

## 6. 鉴权方式

所有 `/v1/*` 接口支持两种鉴权（任选其一），都在 `Authorization: Bearer <凭证>` 头中传递：

### 6.1 API Key（推荐，用于应用对接）

- 格式：`ac_` 开头的长字符串
- 获取：管理后台「设置 → API 密钥」创建
- 特点：**长期有效**，可禁用/删除，可创建多个分给不同应用
- 用法：
  ```bash
  Authorization: Bearer ac_xxxxxxxx
  ```

### 6.2 JWT（用于管理后台）

- 获取：`POST /api/v1/auth/login`，body `{"password": "管理员密码"}`，返回 `{"token": "..."}`
- 特点：有效期 7 天，过期需重新登录
- **不建议用于服务端应用**（会过期，且权限等同管理员）

```bash
# 获取 JWT
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password":"your-admin-password"}' | jq -r .token)

# 用 JWT 调用
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" ...
```

> 区分：`ac_` 开头 → 走 API Key 校验；其余 → 走 JWT 校验。系统自动识别。

---

## 7. 接口参考

详细的字段定义、参数范围请查阅 Swagger：`http://your-host:8000/docs`。以下是要点。

### 7.1 POST /v1/chat/completions

OpenAI 兼容的对话补全。请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | string | **必填**。具体 id 或 auto 前缀（见第 4 节） |
| `messages` | array | **必填**。消息列表，同 OpenAI |
| `stream` | bool | 是否流式，默认 false |
| `max_tokens` | int | 最大生成 token 数 |
| `temperature` | float | 采样温度 |
| `top_p` | float | nucleus sampling |
| `stop` | array | 停止词列表 |

响应格式与 OpenAI 完全一致（含流式 SSE 格式）。

### 7.2 GET /v1/models

返回当前可用的免费、非 down 的对话模型。响应：

```json
{
  "object": "list",
  "data": [
    {
      "id": "meta-llama/llama-3.3-70b-instruct",
      "object": "model",
      "created": 0,
      "owned_by": "available-computing",
      "param_size": 70.0
    }
  ]
}
```

> 注意：`param_size` 是本项目扩展字段（OpenAI 标准无此字段），客户端会自动忽略不影响使用。

### 7.3 管理接口（/api/v1/*）

管理接口（模型列表、厂商管理、密钥管理等）需要 JWT 鉴权，详见 `/docs`。应用对接一般不需要调用这些。

---

## 8. Embedding 与 Rerank

除了对话，本项目还支持 **embedding**（文本向量化）和 **rerank**（文档重排序）两类模型的查询与调用。

> **重要区别**：
> - **embedding** 是 OpenAI 兼容端点（`/v1/embeddings`），多个厂商支持
> - **rerank** 是 **SiliconFlow 兼容端点**（`/v1/rerank`），**不是 OpenAI 官方标准**，目前仅 SiliconFlow 提供免费模型

### 8.1 查询有哪些模型

默认 `/v1/models` 只返回对话模型。用 `category` 参数查询非对话模型：

```bash
# 查 embedding 模型
curl http://localhost:8000/v1/models?category=embedding \
  -H "Authorization: Bearer ac_xxxxxxxx"

# 查 rerank 模型
curl http://localhost:8000/v1/models?category=rerank \
  -H "Authorization: Bearer ac_xxxxxxxx"

# 查所有类别（含 embedding/rerank）
curl http://localhost:8000/v1/models?category=all \
  -H "Authorization: Bearer ac_xxxxxxxx"
```

### 8.2 调用 Embedding

OpenAI 兼容格式。`model` 需填具体模型 id（**不支持 auto 路由**），可从 `?category=embedding` 查到：

```bash
curl http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer ac_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-m3",
    "input": "把这句话转成向量"
  }'
```

Python（OpenAI SDK 原生支持 embeddings）：

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="ac_xxxxxxxx")
resp = client.embeddings.create(
    model="BAAI/bge-m3",
    input="把这句话转成向量",
)
print(resp.data[0].embedding[:5])   # [0.01, -0.03, ...]
```

响应格式与 OpenAI 一致：`{"data": [{"embedding": [0.1, 0.2, ...], "index": 0}], ...}`

### 8.3 调用 Rerank

> ⚠️ `/v1/rerank` 不是 OpenAI 标准端点，OpenAI SDK 不内置支持。需直接 HTTP 调用或用 SiliconFlow/LangChain 的 rerank 工具。

给定一个 query 和一批 documents，返回按相关性排序的结果：

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer ac_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "如何用 Python 读取文件",
    "documents": [
      "Python open() 函数可以打开文件",
      "Java 的 File 类用于文件操作",
      "with open(path) as f 可以安全地读取文件"
    ],
    "top_n": 2,
    "return_documents": true
  }'
```

Python：

```python
import httpx

resp = httpx.post(
    "http://localhost:8000/v1/rerank",
    headers={"Authorization": "Bearer ac_xxxxxxxx"},
    json={
        "model": "BAAI/bge-reranker-v2-m3",
        "query": "如何用 Python 读取文件",
        "documents": [
            "Python open() 函数可以打开文件",
            "Java 的 File 类用于文件操作",
            "with open(path) as f 可以安全地读取文件",
        ],
        "top_n": 2,
    },
    timeout=60,
)
for r in resp.json()["results"]:
    print(r["relevance_score"], r["index"], r.get("document", {}).get("text", ""))
# 0.98 2  with open(path) as f 可以安全地读取文件
# 0.91 0  Python open() 函数可以打开文件
```

响应格式（SiliconFlow 兼容）：

```json
{
  "results": [
    {"index": 2, "relevance_score": 0.98, "document": {"text": "..."}},
    {"index": 0, "relevance_score": 0.91, "document": {"text": "..."}}
  ]
}
```

### 8.4 常见问题

**Q: 为什么 embedding/rerank 模型没有 auto 路由？**
A: 这两类模型用途专一（向量化、排序），通常需要指定具体模型保证向量维度/rerank 行为一致。auto 路由会随机选模型导致向量空间不一致。

**Q: 调用时报 404？**
A: 用 `GET /v1/models?category=embedding`（或 rerank）确认模型 id 是否存在、是否健康。模型 id 支持模糊匹配（如 `bge-m3` 能匹配 `BAAI/bge-m3`）。

**Q: 哪些厂商提供免费 embedding/rerank？**
A: 当前仅 SiliconFlow。其他厂商（OpenRouter/Groq 等）的这类模型非免费，不在池中。

---

## 9. 限流与错误处理

### 9.1 限流

| 维度 | 限制 |
|------|------|
| 对话接口 `/v1/chat/completions` | 60 次/分钟/IP |
| 登录 `/api/v1/auth/login` | 10 次/5 分钟/IP |

超限返回 `429`。

### 9.2 错误响应格式

所有错误遵循 OpenAI 格式：

```json
{
  "error": {
    "message": "No available models for auto:smart",
    "type": "invalid_request_error",
    "param": "model",
    "code": null
  }
}
```

### 9.3 常见错误码

| HTTP | 含义 | 处理建议 |
|------|------|---------|
| 401 | 鉴权失败（Key 无效/过期） | 检查 API Key 是否正确、是否被禁用 |
| 404 | 模型不存在或无可用模型 | 用 `/v1/models` 确认模型名；auto 路由时说明池中暂无健康模型，稍后重试 |
| 429 | 触发限流 | 退避重试（指数退避） |
| 502 | 上游厂商返回错误 | 通常是厂商侧问题，换模型重试或用 `auto:` 让系统自动避让 |

### 9.4 重试建议

对于生产环境，建议：

- **用 auto 路由而非固定模型**：单个模型不可用时，auto 会自动跳过它选别的，无需你处理重试
- 对 `502`/网络错误做**指数退避重试**（1s → 2s → 4s，最多 3 次）
- 对 `429` 做退避，但**降低频率**而非立即重试

---

## 10. 常见问题

**Q: 为什么有时 auto:smart 选到的模型回答质量一般？**
A: smart 按参数量排序，参数量大通常更强，但不绝对（代际更新、模型类型也有影响）。如果对质量敏感，建议在 `/v1/models` 里挑具体的大模型 id 直接调用。

**Q: 模型偶尔返回很慢或超时？**
A: 免费模型有厂商侧的限流（如 OpenRouter 的 daily free quota）。系统会把这类模型标为 slow 降权，但仍在池中。用 `auto:fast` 优先选最快的。

**Q: 能同时用多个模型吗？**
A: 可以。并发请求会路由到不同模型（每次请求独立选模型）。如果想固定，就指定具体 id。

**Q: 支持 function calling / tool use 吗？**
A: 取决于被路由到的具体模型是否支持。本项目透传请求，不额外处理。建议直接填支持 tool 的具体模型 id，而非 auto 路由。

**Q: API Key 泄露了怎么办？**
A: 在管理后台禁用或删除该 Key，立即创建新的。被禁用的 Key 立即失效。

**Q: 如何监控可用性？**
A: 管理后台的「算力池」页面实时展示每个模型的健康状态、延迟、参数量。应用侧建议对 `/v1/models` 做周期性探活。

---

## 附录：相关文档

- [01-PRD.md](./01-PRD.md) —— 产品需求
- [03-architecture.md](./03-architecture.md) —— 系统架构
- [05-deployment.md](./05-deployment.md) —— 部署指南
- Swagger 交互文档 —— `http://your-host:8000/docs`
- 管理后台 —— `http://your-host:5173`
