# 声明式算力提供商

这里的 YAML 用于接入标准 OpenAI Chat Completions 接口。应用启动时会严格校验全部配置；任一文件非法、ID 重复或覆盖内置适配器时，启动会直接失败。

安全边界：

- 只允许 HTTPS Base URL、相对 API 路径和 Bearer Token 鉴权。
- 免费模型必须进入 `free_detection.model_ids` 允许列表；目录中的其他模型明确不参与免费路由。
- `model_overrides` 只能描述允许列表中的模型。
- `compliance.sources` 和 `reviewed_at` 记录人工复核依据；上游政策变化后应重新复核。
- 复杂鉴权、非标准请求体或专用免费目录应继续使用 Python 定制适配器。

当前通用路径由 Mistral 配置覆盖。新厂商只有在确认无需信用卡、不是一次性
赠金或限时试用后才能加入；社区清单中的候选项不能直接转成正式配置。
