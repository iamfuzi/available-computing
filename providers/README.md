# 声明式算力提供商

这里的 YAML 用于接入标准 OpenAI Chat Completions 接口。应用启动时会严格校验全部配置；任一文件非法、ID 重复或覆盖内置适配器时，启动会直接失败。

安全边界：

- 只允许 HTTPS Base URL 与相对 API 路径；鉴权支持 Bearer Token 或明确声明的无鉴权模式。
- 免费判定只允许人工审定的 `model_ids` 允许列表，或有官方语义的模型 ID 后缀；目录中的其他模型不参与免费路由。
- `model_overrides` 只能描述免费判定能够命中的模型。
- `compliance.sources` 和 `reviewed_at` 记录人工复核依据；上游政策变化后应重新复核。
- 复杂鉴权、非标准请求体或专用免费目录应继续使用 Python 定制适配器。

当前通用路径覆盖 Mistral AI 和匿名 Kilo Gateway。新厂商只有在确认无需信用卡、不是一次性赠金、固定赠送额度或限时试用，且不会自动转付费后才能加入；社区清单中的候选项不能直接转成正式配置。

## 配置要点

- `auth.type: bearer` 需要用户提供 Key；`auth.type: none` 必须同时把 `setup.key_optional` 设为 `true`。
- `free_detection.method: allowlist` 使用 `model_ids`；`id_suffix` 使用经官方文档确认的后缀，并可附加少量显式 ID。
- `requirements` 记录信用卡、手机和实名要求；这些字段不能替代官方条款复核。
- 任一 YAML 校验失败、ID 重复或覆盖内置 Adapter 时，应用会拒绝启动，以免静默加载错误策略。
