# 第三方接入指南

Available Computing 提供 OpenAI 兼容接口，推荐第三方服务优先使用
`auto:*` 路由，而不是固定调用某个免费模型。

## 快速开始

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer ac_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto:text",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

推荐路由：

| 路由 | 用途 |
| --- | --- |
| `auto:text` | 默认文本对话 |
| `auto:fast` | 优先低延迟 |
| `auto:smart` | 优先较大参数模型 |
| `auto:vision` | 多模态理解 |
| `auto:code` | 代码生成 |

## 诊断 Header

代理响应会带上 `X-AC-*` header，便于排查调度行为：

| Header | 说明 |
| --- | --- |
| `X-AC-Route` | 请求使用的模型或 auto 路由 |
| `X-AC-Selected-Model` | 最终命中的上游模型 |
| `X-AC-Selected-Provider` | 最终命中的上游厂商 |
| `X-AC-Attempted-Models` | 本次尝试过的候选模型 |
| `X-AC-Fallback-Count` | fallback 次数 |
| `X-AC-Retry-After` | 建议等待秒数 |

## 错误处理

错误响应统一为：

```json
{
  "error": {
    "message": "All attempted candidate free models are currently rate limited",
    "type": "rate_limit_error",
    "code": "all_candidates_rate_limited",
    "retry_after": 60,
    "attempted_models": ["model-a", "model-b"]
  }
}
```

常见错误码：

| code | 建议处理 |
| --- | --- |
| `model_not_found` | 改用 `/v1/models` 或 `auto:*` |
| `no_available_models` | 等待健康探测或添加更多厂商 Key |
| `all_candidates_rate_limited` | 尊重 `retry_after` 后重试 |
| `local_rate_limited` | 降低当前 API Key 的调用频率 |
| `local_model_budget_exceeded` | 当前模型本地预算已满，优先使用 `auto:*` |
| `all_candidates_busy` | 降低并发，稍后重试 |
| `upstream_auth_failed` | 检查上游厂商 Key 或账号状态 |
| `upstream_server_error` | 稍后重试 |

## 机器可读诊断接口

```bash
curl http://localhost:8080/v1/ac/status \
  -H "Authorization: Bearer ac_your_key"

curl http://localhost:8080/v1/ac/models \
  -H "Authorization: Bearer ac_your_key"
```

`/v1/ac/status` 返回可用模型数量、限流分布和 `auto:*` 路由状态。

`/v1/ac/models` 返回完整模型状态，包括 `route_eligible`、
`rate_limited_until`、`last_success_at` 和 `last_response_ms`。

## 接入自检

```bash
curl http://localhost:8080/v1/ac/self-test \
  -H "Authorization: Bearer ac_your_key" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto:text"}'
```

自检不会调用上游模型，不消耗免费额度。它只检查当前路由是否有可用候选、
本地预算是否已满，以及首选模型是什么。

## 生产建议

- 默认调用 `auto:text` 或 `auto:fast`。
- 不要在第三方侧固定单个免费模型，除非你能处理它的限流和下线。
- 对 `429` 读取 `retry_after` 或 `X-AC-Retry-After`。
- 对 `503 all_candidates_busy` 使用短延迟重试，并降低并发。
- 保存 `X-AC-Selected-Model` 和 `X-AC-Attempted-Models` 进调用日志。
- 客户端设置 60-120 秒超时；流式请求要处理断连。
