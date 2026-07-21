"""Validated configuration for simple OpenAI-compatible providers.

The format is intentionally narrow: configuration may describe documented
HTTPS endpoints and response field paths, but cannot provide executable code
or arbitrary request templates.
"""

from pathlib import Path
import re
from typing import Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthConfig(StrictModel):
    type: Literal["bearer", "none"] = "bearer"
    header: Literal["Authorization"] = "Authorization"


class EndpointConfig(StrictModel):
    models: str = "/models"
    chat_completions: str = "/chat/completions"

    @field_validator("models", "chat_completions")
    @classmethod
    def validate_relative_endpoint(cls, value: str) -> str:
        if not value.startswith("/") or "://" in value or ".." in value:
            raise ValueError("endpoint must be a safe absolute path")
        return value


class ModelMappingConfig(StrictModel):
    items_path: str = "data"
    id_path: str = "id"
    display_name_path: str = "id"
    context_length_path: Optional[str] = None
    category_path: Optional[str] = None
    vision_capability_path: Optional[str] = None


class FreeDetectionConfig(StrictModel):
    method: Literal["allowlist", "id_suffix"] = "allowlist"
    model_ids: list[str] = Field(default_factory=list)
    id_suffix: Optional[str] = None
    free_type: Literal["permanent", "quota", "trial"] = "quota"

    @field_validator("model_ids")
    @classmethod
    def no_duplicate_models(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("free model allowlist contains duplicates")
        return value

    @model_validator(mode="after")
    def validate_detection_rule(self):
        if self.method == "allowlist" and not self.model_ids:
            raise ValueError("allowlist free detection requires model_ids")
        if self.method == "id_suffix" and not self.id_suffix:
            raise ValueError("id_suffix free detection requires id_suffix")
        return self


class ModelOverrideConfig(StrictModel):
    display_name: Optional[str] = None
    category: Optional[Literal["text", "vision", "code", "embedding", "audio", "rerank"]] = None
    context_length: Optional[int] = Field(default=None, gt=0)
    rate_limit: Optional[dict[str, int]] = None


class ProbeConfig(StrictModel):
    prompt: str = "Reply with OK"
    max_tokens: int = Field(default=8, ge=1, le=64)


class RequirementsConfig(StrictModel):
    requires_card: bool = False
    requires_phone: bool = False
    requires_realname: bool = False


class SetupConfig(StrictModel):
    description: str
    key_hint: str
    console_url: str
    key_optional: bool = False

    @field_validator("console_url")
    @classmethod
    def validate_console_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("console_url must be HTTPS")
        return value


class ComplianceConfig(StrictModel):
    risk: Literal["low", "medium", "unknown"] = "unknown"
    note: str
    reviewed_at: str
    sources: list[str] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("compliance sources must be HTTPS URLs")
        return values


class DeclarativeProviderConfig(StrictModel):
    version: Literal[1] = 1
    id: str
    name: str
    config_type: Literal["declarative"] = "declarative"
    base_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    endpoints: EndpointConfig = Field(default_factory=EndpointConfig)
    model_mapping: ModelMappingConfig = Field(default_factory=ModelMappingConfig)
    free_detection: FreeDetectionConfig
    model_overrides: dict[str, ModelOverrideConfig] = Field(default_factory=dict)
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    requirements: RequirementsConfig = Field(default_factory=RequirementsConfig)
    setup: SetupConfig
    compliance: ComplianceConfig

    @field_validator("id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        if not _PROVIDER_ID.fullmatch(value):
            raise ValueError("invalid provider id")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("base_url must be a plain HTTPS URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_overrides(self):
        if self.free_detection.method == "allowlist":
            allowed = set(self.free_detection.model_ids)
            unexpected = set(self.model_overrides) - allowed
            if unexpected:
                raise ValueError(
                    "model_overrides must only describe allowlisted free models: "
                    + ", ".join(sorted(unexpected))
                )
        return self


def load_declarative_providers(path: Path) -> list[DeclarativeProviderConfig]:
    """Load and validate every provider YAML file in deterministic order."""
    if not path.exists():
        return []
    if not path.is_dir():
        raise ValueError(f"PROVIDERS_PATH is not a directory: {path}")

    configs: list[DeclarativeProviderConfig] = []
    seen: set[str] = set()
    for config_path in sorted((*path.glob("*.yaml"), *path.glob("*.yml"))):
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"provider config must be a mapping: {config_path}")
        try:
            config = DeclarativeProviderConfig.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"invalid provider config {config_path}: {exc}") from exc
        if config.id in seen:
            raise ValueError(f"duplicate declarative provider id: {config.id}")
        seen.add(config.id)
        configs.append(config)
    return configs
