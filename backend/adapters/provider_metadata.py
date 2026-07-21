"""Human-reviewed onboarding and compliance metadata for custom adapters.

Declarative providers carry the same structure in their YAML. Keeping custom
adapter metadata here gives every provider one auditable review record without
mixing legal/product notes into request-handling code.
"""

CUSTOM_PROVIDER_METADATA: dict[str, dict] = {
    "groq": {
        "requirements": {
            "requires_card": False,
            "requires_phone": False,
            "requires_realname": False,
        },
        "setup": {
            "description": "GroqCloud 免费开发额度，无需信用卡",
            "key_hint": "GroqCloud Console → API Keys → Create API Key",
            "console_url": "https://console.groq.com/keys",
        },
        "compliance": {
            "risk": "medium",
            "note": "官方 Cloud Services 条款限制未经批准的出售、转售、再许可、转让或分发。个人自用代理不等同于向第三方转售，但不得共享上游 Key 或把本服务开放给第三方。",
            "reviewed_at": "2026-07-21",
            "sources": ["https://console.groq.com/docs/legal/services-agreement"],
        },
    },
    "siliconflow": {
        "requirements": {
            "requires_card": False,
            "requires_phone": True,
            "requires_realname": False,
        },
        "setup": {
            "description": "硅基流动提供部分永久免费模型",
            "key_hint": "SiliconCloud 控制台 → API 密钥",
            "console_url": "https://cloud.siliconflow.cn/account/ak",
        },
        "compliance": {
            "risk": "unknown",
            "note": "公开文档中未查到对个人自用 API 聚合转发的明确授权或禁止条款；仅限个人自用，不转售、不共享上游 Key，条款变化后需复核。",
            "reviewed_at": "2026-07-21",
            "sources": ["https://docs.siliconflow.cn/"],
        },
    },
    "openrouter": {
        "requirements": {
            "requires_card": False,
            "requires_phone": False,
            "requires_realname": False,
        },
        "setup": {
            "description": "聚合多个提供商，并提供带 :free 标记的免费模型",
            "key_hint": "OpenRouter → Settings → API Keys",
            "console_url": "https://openrouter.ai/settings/keys",
        },
        "compliance": {
            "risk": "high",
            "note": "当前条款明确禁止为转售模型 API 访问或开发竞争服务而使用；同时要求下游用户遵守各模型条款。本项目只应作为单人自用工具，不应对第三方提供服务。",
            "reviewed_at": "2026-07-21",
            "sources": ["https://openrouter.ai/terms"],
        },
    },
    "zhipu": {
        "requirements": {
            "requires_card": False,
            "requires_phone": True,
            "requires_realname": False,
        },
        "setup": {
            "description": "智谱开放平台提供 GLM Flash、CogView Flash 等免费模型",
            "key_hint": "智谱开放平台 → API Keys",
            "console_url": "https://open.bigmodel.cn/usercenter/apikeys",
        },
        "compliance": {
            "risk": "medium",
            "note": "开放平台用户协议授予不可转让、不可转许可的使用许可，并限制未经明示同意的转让、出售或提供他人使用。个人本机调用可继续使用，禁止向第三方转售或共享账户权益。",
            "reviewed_at": "2026-07-21",
            "sources": ["https://docs.bigmodel.cn/cn/terms/user-agreement"],
        },
    },
    "agnes": {
        "requirements": {
            "requires_card": False,
            "requires_phone": False,
            "requires_realname": False,
        },
        "setup": {
            "description": "Agnes AI 提供免费 Flash 文本与图像理解模型",
            "key_hint": "Agnes AI API Hub → API Key",
            "console_url": "https://app.agnes-ai.com/",
        },
        "compliance": {
            "risk": "unknown",
            "note": "未找到可公开访问且明确覆盖 API 聚合转发的官方服务条款。接入仅限个人自用，不共享上游 Key；在发现正式条款后必须重新审核。",
            "reviewed_at": "2026-07-21",
            "sources": ["https://agnes-ai.com/"],
        },
    },
}
