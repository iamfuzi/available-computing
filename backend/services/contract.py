"""服务契约：机器可读的调用方适配依据。

纯文本公告无法被没有大模型的程序"理解"——调用方需要的是确定性信号：
契约版本号 + 错误码语义表。约定（docs/06 §1.4）：
- 调用方钉住自己验证过的 contract_version；
- 版本变化（self-test 响应 / GET /public/contract 轮询）即触发调用方
  自己的升级流程：报警、跑集成自检、或人工确认；
- 每个错误码的 retryable 与建议动作在表内声明，AC 侧改动错误语义
  必须 bump CONTRACT_VERSION 并公告 change_type=error_code_change。

CONTRACT_VERSION 变更记录：
- 1  2026-10-01  初版：错误码表、auto 路由承诺、限额默认值
"""

CONTRACT_VERSION = 1

# 调用方处置手册（docs/06 同步维护）。recommended_action 为枚举，程序
# 可直接映射到自己的策略；说明字段面向运维人员。
ERROR_CODES: dict[str, dict] = {
    "model_not_found": {
        "retryable": False,
        "recommended_action": "fix_request",
        "note": "模型 id 不存在或当前不可路由；按响应提示改用 /v1/models 列表或 auto:*",
    },
    "local_rate_limited": {
        "retryable": True,
        "recommended_action": "retry_after",
        "note": "触发 Key 级本地限流；读 Retry-After 退避",
    },
    "local_model_budget_exceeded": {
        "retryable": True,
        "recommended_action": "retry_after",
        "note": "单模型本地预算满；退避或换 auto:*",
    },
    "local_provider_rpm_exceeded": {
        "retryable": True,
        "recommended_action": "retry_after",
        "note": "渠道聚合 RPM 本地保护；退避",
    },
    "local_provider_tpm_exceeded": {
        "retryable": True,
        "recommended_action": "retry_after",
        "note": "渠道 token/分钟本地保护；退避或减小请求体",
    },
    "all_candidates_busy": {
        "retryable": True,
        "recommended_action": "retry_later",
        "note": "候选全忙（槽位排队满）；稍后重试",
    },
    "all_candidates_rate_limited": {
        "retryable": True,
        "recommended_action": "retry_after",
        "note": "尝试过的候选全部上游 429；读 Retry-After",
    },
    "all_candidates_unavailable": {
        "retryable": True,
        "recommended_action": "retry_later",
        "note": "候选存在但暂不可用；稍后重试或缩小范围",
    },
    "all_candidates_empty_content": {
        "retryable": True,
        "recommended_action": "raise_budget",
        "note": "全部候选正文为空（思考烧光输出预算）；增大 max_tokens 后重试",
    },
    "upstream_error": {
        "retryable": True,
        "recommended_action": "retry_later",
        "note": "上游故障；有限重试",
    },
    "upstream_auth_failed": {
        "retryable": False,
        "recommended_action": "contact_admin",
        "note": "上游鉴权问题（AC 侧渠道）；联系管理者",
    },
}

# 公告的机器可判类型；每个类型对应 docs/06 的处置手册条目
CHANGE_TYPES = (
    "error_code_change",   # 错误码增删或语义变化 → 对照契约表
    "behavior_change",     # 行为变化（如 auto 路由规则）→ 跑集成自检
    "deprecation",         # 即将下线 → 计划迁移
    "maintenance",         # 维护窗口 → 暂停调度或排队
    "new_endpoint",        # 新能力 → 可选接入
    "limit_change",        # 限额变化 → 校准本地节流
    "other",
)


# 每类公告的确定性处置动作（docs/06 §1.4 同源）。调用方告警触发后可
# 从契约端点动态拉取，无需存档手册。
PLAYBOOK = {
    "error_code_change": {
        "action": "fetch_error_codes_and_diff",
        "note": "拉取契约 error_codes 对照自身分支；未知码按 retryable 兜底",
    },
    "behavior_change": {
        "action": "run_integration_selftest",
        "note": "固定用例跑集成自检；异常则人工介入",
    },
    "deprecation": {
        "action": "alert_and_schedule_migration",
        "note": "告警并按 affected 排期迁移",
    },
    "maintenance": {
        "action": "pause_or_queue",
        "note": "窗口内暂停调度或入队（affected 含时间）",
    },
    "new_endpoint": {
        "action": "optional_adopt",
        "note": "可选接入，无动作要求",
    },
    "limit_change": {
        "action": "recalibrate_local_throttle",
        "note": "按新限额校准本地节流",
    },
    "other": {
        "action": "alert_human",
        "note": "人工阅读公告正文",
    },
}


def contract_payload() -> dict:
    from config import PROXY_API_KEY_RATE_LIMIT
    return {
        "contract_version": CONTRACT_VERSION,
        "error_codes": ERROR_CODES,
        "change_types": list(CHANGE_TYPES),
        "playbook": PLAYBOOK,
        "auto_route_promise": {
            "clean_content": True,
            "note": "auto:* 返回的 message.content 直接可用；不路由内联思考模型",
        },
        "limits": {
            "key_default_rpm": PROXY_API_KEY_RATE_LIMIT,
            "window_seconds": 60,
            "exceed_behavior": "429 + Retry-After",
        },
        "contract_url": "/api/v1/auth/public/contract",
    }
