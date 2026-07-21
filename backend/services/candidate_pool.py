"""Fetch community free-API lists into a review-only candidate pool."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from sqlmodel import Session, select

from adapters import list_providers
from config import DATA_DIR
from database import engine
from models import CandidateProvider, CandidateSourceState


SOURCES = {
    "awesome_free_llm_apis": "https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/README.md",
    "free_llm_api_resources": "https://raw.githubusercontent.com/cheahjs/free-llm-api-resources/main/README.md",
}
MIN_REASONABLE_CANDIDATES = 3

_ALIASES = {
    "google-ai-studio": "google-gemini",
    "google-gemini": "google-gemini",
    "z-ai-zhipu-ai": "zhipu",
    "mistral-la-plateforme": "mistral",
    "mistral-ai": "mistral",
    "mistral-codestral": "mistral",
    "huggingface-inference-providers": "huggingface",
    "hugging-face": "huggingface",
    "nvidia-nim": "nvidia",
    "github-models": "github-models",
    "cloudflare-workers-ai": "cloudflare",
    "siliconflow": "siliconflow",
}

# Overrides are based on official review, not community labels. They prevent a
# stale README from re-admitting services already known to violate the local
# no-card/no-trial policy.
_ADMISSION_OVERRIDES = {
    "aion-labs": ("quota_limited", False, "免费层为每日 credit/token allowance，且有每日 token 上限"),
    "cloudflare": ("quota_limited", False, "Workers AI 免费分配量固定为每日 10,000 Neurons"),
    "github-models": ("quota_limited", False, "免费用量按账户和账期配额计量"),
    "llm7-io": ("quota_limited", False, "免费 token 每 24 小时固定上限为 1,000,000"),
    "modelscope": ("quota_limited", False, "API-Inference 固定为每日 2,000 次、单模型每日 200 次"),
    "ollama-cloud": ("quota_limited", False, "免费 Cloud 计划有 5 小时会话上限和每周上限"),
    "opencode-zen": ("trial_credit", True, "免费模型均明确为限时开放，使用 Zen 还需添加账单信息"),
    "ovhcloud-ai-endpoints": ("credit_metered", False, "当前通用 LLM 已按输入/输出 token 定价；免费项仅为 Beta Guard 模型"),
    "sambanova": ("card_required", True, "当前免费账户需添加支付方式并购买 credits 才能发起请求"),
    "cerebras": ("trial_credit", True, "需要验证支付方式，且免费能力属于试用额度"),
    "google-gemini": ("quota_limited", False, "用户已明确排除有固定免费配额的新厂商"),
    "nvidia": ("trial_credit", False, "受 NVIDIA API Trial Terms 约束"),
    "cohere": ("trial_credit", False, "社区证据明确标为 Trial API key"),
    "huggingface": ("credit_metered", False, "免费能力按月度 credits 计量"),
    "vercel-ai-gateway": ("credit_metered", False, "免费能力按月度 credits 计量"),
}

_ADMISSION_APPROVALS = {
    "kilo-code": ("recurring_free", False),
}

_OFFICIAL_REVIEW_SOURCES = {
    "aion-labs": ["https://www.aionlabs.ai/pricing/", "https://www.aionlabs.ai/docs/rate-limits/"],
    "cloudflare": ["https://developers.cloudflare.com/workers-ai/platform/pricing/"],
    "github-models": ["https://docs.github.com/en/billing/concepts/product-billing/github-models"],
    "kilo-code": [
        "https://kilo.ai/terms",
        "https://kilo.ai/docs/gateway/authentication",
        "https://kilo.ai/docs/gateway/usage-and-billing",
    ],
    "llm7-io": ["https://docs.llm7.io/limits"],
    "modelscope": ["https://www.modelscope.cn/docs/model-service/API-Inference/limits"],
    "ollama-cloud": ["https://ollama.com/pricing"],
    "opencode-zen": ["https://opencode.ai/docs/zen/"],
    "ovhcloud-ai-endpoints": ["https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/"],
    "sambanova": ["https://cloud.sambanova.ai/plans"],
}


@dataclass
class ParsedCandidate:
    provider_id: str
    name: str
    homepage_url: str
    source_id: str
    base_url: str | None = None
    summary: str = ""
    models: list[str] = field(default_factory=list)
    raw_section: str = ""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return _ALIASES.get(slug, slug)[:64]


def _clean_cell(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).replace("`", "").strip()


def _extract_models(section: str) -> list[str]:
    models: list[str] = []
    for line in section.splitlines():
        if line.startswith("|"):
            cells = [_clean_cell(cell) for cell in line.strip().strip("|").split("|")]
            first = cells[0] if cells else ""
            if first and not re.fullmatch(r"[-: ]+", first) and first.lower() not in {"model", "model name"}:
                models.append(first)
        elif re.match(r"^- \[.+\]\(https?://", line):
            models.append(re.match(r"^- \[([^]]+)\]", line).group(1))
    for value in re.findall(r"<tr><td>(.*?)</td>", section, flags=re.I | re.S):
        name = _clean_cell(value)
        if name and name.lower() != "model name":
            models.append(name)
    return list(dict.fromkeys(models))


def parse_markdown_candidates(markdown: str, source_id: str) -> list[ParsedCandidate]:
    """Parse provider level-3 sections without trusting any one table layout."""
    heading = re.compile(r"^### \[([^]]+)\]\((https?://[^)]+)\).*$", re.M)
    matches = list(heading.finditer(markdown))
    candidates: list[ParsedCandidate] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        next_level_two = re.search(r"^## [^#]", markdown[start:end], flags=re.M)
        if next_level_two:
            end = start + next_level_two.start()
        section = markdown[start:end].strip()
        # cheahjs explicitly separates perpetual free providers from trials.
        prefix = markdown[:match.start()]
        if "## Providers with trial credits" in prefix:
            continue
        name, homepage = match.group(1).strip(), match.group(2).strip()
        provider_id = _slugify(name)
        if not provider_id:
            continue
        base_match = re.search(r"Base URL:\s*`([^`]+)`", section, flags=re.I)
        base_url = base_match.group(1).strip() if base_match else None
        summary = next(
            (
                line.strip()
                for line in section.splitlines()
                if line.strip()
                and not line.startswith(("|", "-", "<", "**", "#"))
                and "Base URL:" not in line
            ),
            "",
        )
        candidates.append(
            ParsedCandidate(
                provider_id=provider_id,
                name=name,
                homepage_url=homepage,
                source_id=source_id,
                base_url=base_url,
                summary=summary,
                models=_extract_models(section),
                raw_section=section[:12000],
            )
        )
    return candidates


def _compatibility(candidate: ParsedCandidate) -> str:
    if candidate.base_url and "{" not in candidate.base_url:
        path = urlparse(candidate.base_url).path.rstrip("/")
        if path.endswith(("/v1", "/v2", "/inference", "/v4")):
            return "openai_compatible"
    return "special_or_unknown"


def _admission_policy(candidate: ParsedCandidate) -> tuple[str, bool, str, str | None]:
    approval = _ADMISSION_APPROVALS.get(candidate.provider_id)
    if approval:
        access_type, requires_card = approval
        return access_type, requires_card, "review_required", None
    override = _ADMISSION_OVERRIDES.get(candidate.provider_id)
    if override:
        access_type, requires_card, reason = override
        return access_type, requires_card, "excluded", reason

    evidence = candidate.raw_section.lower()
    no_card = "no credit card" in evidence or "without a credit card" in evidence
    requires_card = (
        "credit card required" in evidence
        or "requires a credit card" in evidence
        or "payment method required" in evidence
    ) and not no_card
    trial_markers = (
        'free "trial"',
        "trial credits",
        "trial credit",
        "providers with trial",
        "one-time credit",
    )
    credit_metered = "credits/month" in evidence or "monthly credits" in evidence
    if requires_card:
        return "card_required", True, "excluded", "需要信用卡或支付方式"
    if any(marker in evidence for marker in trial_markers):
        return "trial_credit", False, "excluded", "一次性赠金或限时试用"
    if credit_metered:
        return "credit_metered", False, "excluded", "免费能力按 credits 计量"
    if "permanent free" in evidence or "permanently free" in evidence:
        return "permanent_free", False, "review_required", None
    if "free tier" in evidence or "currently free" in evidence:
        return "recurring_free", False, "review_required", None
    return "unknown", False, "review_required", None


def _yaml_draft(candidate: ParsedCandidate) -> str:
    model_ids = candidate.models[:20] or ["TODO_MODEL_ID"]
    draft = {
        "version": 1,
        "id": candidate.provider_id,
        "name": candidate.name,
        "config_type": "declarative",
        "base_url": candidate.base_url or "https://TODO.example/v1",
        "auth": {"type": "bearer", "header": "Authorization"},
        "endpoints": {"models": "/models", "chat_completions": "/chat/completions"},
        "model_mapping": {"items_path": "data", "id_path": "id", "display_name_path": "id"},
        "free_detection": {"method": "allowlist", "model_ids": model_ids, "free_type": "quota"},
        "probe": {"prompt": "Reply with OK", "max_tokens": 8},
        "requirements": {
            "requires_card": "credit card" in candidate.raw_section.lower() and "no credit card" not in candidate.raw_section.lower(),
            "requires_phone": "phone" in candidate.raw_section.lower(),
            "requires_realname": "real-name" in candidate.raw_section.lower(),
        },
        "setup": {
            "description": candidate.summary or "TODO: review free-tier conditions",
            "key_hint": "TODO: document API key creation",
            "console_url": candidate.homepage_url,
        },
        "compliance": {
            "risk": "unknown",
            "note": "TODO: review official terms before enabling; community lists are discovery evidence only.",
            "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
            "sources": [candidate.homepage_url],
        },
    }
    return yaml.safe_dump(draft, allow_unicode=True, sort_keys=False)


def _mark_source_failure(source_id: str, url: str, error: str) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        state = session.get(CandidateSourceState, source_id) or CandidateSourceState(source_id=source_id, url=url)
        state.url = url
        state.last_attempt_at = now
        state.consecutive_failures += 1
        state.last_error = error[:1000]
        state.needs_attention = state.consecutive_failures >= 2
        session.add(state)
        session.commit()


def _persist_source(source_id: str, url: str, candidates: list[ParsedCandidate]) -> None:
    now = datetime.now(timezone.utc)
    configured = {provider["id"] for provider in list_providers()}
    with Session(engine) as session:
        state = session.get(CandidateSourceState, source_id) or CandidateSourceState(source_id=source_id, url=url)
        state.url = url
        state.last_attempt_at = now
        state.last_success_at = now
        state.consecutive_failures = 0
        state.last_error = None
        state.last_candidate_count = len(candidates)
        state.needs_attention = False
        session.add(state)

        for candidate in candidates:
            row = session.get(CandidateProvider, candidate.provider_id)
            if row is None:
                row = CandidateProvider(
                    provider_id=candidate.provider_id,
                    name=candidate.name,
                    homepage_url=candidate.homepage_url,
                )
            old_fingerprint = (row.name, row.base_url, row.models_json, row.summary)
            sources = set(json.loads(row.sources_json or "[]"))
            sources.add(source_id)
            evidence = json.loads(row.evidence_json or "{}")
            evidence[source_id] = candidate.raw_section
            if candidate.provider_id in _OFFICIAL_REVIEW_SOURCES:
                evidence["official_review"] = {
                    "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
                    "sources": _OFFICIAL_REVIEW_SOURCES[candidate.provider_id],
                }
            models = set(json.loads(row.models_json or "[]"))
            models.update(candidate.models)
            row.name = candidate.name
            row.homepage_url = candidate.homepage_url
            row.base_url = candidate.base_url or row.base_url
            row.summary = candidate.summary or row.summary
            observed_compatibility = _compatibility(candidate)
            if observed_compatibility == "openai_compatible" or row.compatibility == "unknown":
                row.compatibility = observed_compatibility
            access_type, requires_card, admission_status, exclusion_reason = _admission_policy(candidate)
            row.access_type = access_type
            row.requires_card = requires_card
            row.admission_status = admission_status
            row.exclusion_reason = exclusion_reason
            row.models_json = json.dumps(sorted(models), ensure_ascii=False)
            row.model_count = len(models)
            row.sources_json = json.dumps(sorted(sources), ensure_ascii=False)
            row.evidence_json = json.dumps(evidence, ensure_ascii=False)
            row.yaml_draft = _yaml_draft(candidate)
            row.is_present = True
            row.last_seen_at = now
            if candidate.provider_id in configured:
                row.status = "configured"
            elif row.status == "configured":
                row.status = "pending"
            new_fingerprint = (row.name, row.base_url, row.models_json, row.summary)
            if new_fingerprint != old_fingerprint:
                row.last_changed_at = now
            session.add(row)
        session.commit()


def _mark_absent_candidates(seen_provider_ids: set[str]) -> None:
    """Only called after every source succeeds, so partial failures retain data."""
    with Session(engine) as session:
        rows = session.exec(select(CandidateProvider)).all()
        for row in rows:
            should_be_present = row.provider_id in seen_provider_ids
            if row.is_present != should_be_present:
                row.is_present = should_be_present
                session.add(row)
        session.commit()


def _write_reports() -> None:
    report_dir = Path(DATA_DIR) / "candidates"
    report_dir.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        rows = session.exec(
            select(CandidateProvider)
            .where(CandidateProvider.is_present == True)
            .order_by(CandidateProvider.status, CandidateProvider.name)
        ).all()
    data = [
        {
            "provider_id": row.provider_id,
            "name": row.name,
            "status": row.status,
            "homepage_url": row.homepage_url,
            "base_url": row.base_url,
            "compatibility": row.compatibility,
            "access_type": row.access_type,
            "requires_card": row.requires_card,
            "admission_status": row.admission_status,
            "exclusion_reason": row.exclusion_reason,
            "model_count": row.model_count,
            "sources": json.loads(row.sources_json),
        }
        for row in rows
    ]
    (report_dir / "latest.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "providers": data}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# 候选厂商 Diff", "", f"生成时间：{datetime.now(timezone.utc).isoformat()}", ""]
    for item in data:
        marker = "已接入" if item["status"] == "configured" else "待审核"
        lines.append(f"- **{item['name']}** (`{item['provider_id']}`) — {marker}，{item['model_count']} 个模型，{item['compatibility']}")
    (report_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def refresh_candidate_pool() -> dict:
    """Refresh successful sources independently; failures never erase old data."""
    successes: dict[str, int] = {}
    failures: dict[str, str] = {}
    seen_provider_ids: set[str] = set()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for source_id, url in SOURCES.items():
            try:
                response = await client.get(url)
                response.raise_for_status()
                candidates = parse_markdown_candidates(response.text, source_id)
                if len(candidates) < MIN_REASONABLE_CANDIDATES:
                    raise ValueError(f"implausible candidate count: {len(candidates)}")
                _persist_source(source_id, url, candidates)
                seen_provider_ids.update(candidate.provider_id for candidate in candidates)
                successes[source_id] = len(candidates)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                _mark_source_failure(source_id, url, message)
                failures[source_id] = message
    if successes and not failures:
        _mark_absent_candidates(seen_provider_ids)
    if successes:
        _write_reports()
    # Turn pending candidates and repeated source failures into persistent,
    # deduplicated administrator notifications.
    from services.notifications import reconcile_notifications
    with Session(engine) as session:
        reconcile_notifications(session)
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return {"successes": successes, "failures": failures}
