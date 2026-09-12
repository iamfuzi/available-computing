from pathlib import Path

import httpx
import pytest
import respx

from adapters import get_adapter, list_providers
from adapters.declarative import DeclarativeAdapter
from adapters.declarative_config import (
    DeclarativeProviderConfig,
    load_declarative_providers,
)
from config import PROVIDERS_PATH


def _mistral_adapter() -> DeclarativeAdapter:
    adapter = get_adapter("mistral")
    assert isinstance(adapter, DeclarativeAdapter)
    return adapter


def test_repository_provider_configs_are_valid_and_unique():
    configs = load_declarative_providers(PROVIDERS_PATH)
    assert [config.id for config in configs] == ["kilo-code", "mistral", "xfyun"]
    assert all(config.base_url.startswith("https://") for config in configs)


def test_config_rejects_unsafe_endpoint():
    raw = {
        "version": 1,
        "id": "unsafe",
        "name": "Unsafe",
        "base_url": "https://example.com/v1",
        "endpoints": {"models": "https://evil.test/models"},
        "free_detection": {"model_ids": ["free-model"]},
        "setup": {
            "description": "test",
            "key_hint": "test",
            "console_url": "https://example.com/keys",
        },
        "compliance": {
            "note": "test",
            "reviewed_at": "2026-07-20",
            "sources": ["https://example.com/docs"],
        },
    }
    with pytest.raises(ValueError, match="safe absolute path"):
        DeclarativeProviderConfig.model_validate(raw)


def test_loader_rejects_duplicate_provider_ids(tmp_path: Path):
    source = (PROVIDERS_PATH / "mistral.yaml").read_text(encoding="utf-8")
    (tmp_path / "one.yaml").write_text(source, encoding="utf-8")
    (tmp_path / "two.yml").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate declarative provider id"):
        load_declarative_providers(tmp_path)


@pytest.mark.asyncio
@respx.mock
async def test_list_models_maps_catalog_and_only_allowlisted_model_is_free():
    adapter = _mistral_adapter()
    respx.get("https://api.mistral.ai/v1/models").mock(return_value=httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "mistral-small-latest",
                    "max_context_length": 32768,
                    "capabilities": {"vision": False},
                },
                {
                    "id": "pixtral-large-latest",
                    "max_context_length": 131072,
                    "capabilities": {"vision": True},
                },
            ]
        },
    ))

    models = await adapter.list_models("secret", adapter.default_base_url)

    assert models[0].display_name == "Mistral Small (latest)"
    assert models[0].context_length == 32768
    assert models[1].category == "vision"
    assert adapter.detect_free_from_api(models[0]) == {
        "is_free": True,
        "free_type": "quota",
        "free_source": "declarative_allowlist",
    }
    assert adapter.detect_free_from_api(models[1]) == {
        "is_free": False,
        "free_source": "declarative_allowlist",
    }


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_uses_bearer_auth():
    route = respx.get("https://api.mistral.ai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await _mistral_adapter().validate_key("top-secret", "https://api.mistral.ai/v1")
    assert route.calls[0].request.headers["Authorization"] == "Bearer top-secret"


@pytest.mark.asyncio
@respx.mock
async def test_anonymous_provider_sends_no_authorization_header():
    adapter = get_adapter("kilo-code")
    route = respx.get("https://api.kilo.ai/api/gateway/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await adapter.validate_key("", adapter.default_base_url)
    assert "Authorization" not in route.calls[0].request.headers
    assert "Authorization" not in adapter.request_headers("")


def test_bearer_provider_exposes_proxy_auth_header():
    adapter = _mistral_adapter()
    assert adapter.request_headers("top-secret") == {
        "Authorization": "Bearer top-secret"
    }
    assert adapter.requires_api_key is True


def test_anonymous_provider_does_not_claim_to_have_a_key():
    assert get_adapter("kilo-code").requires_api_key is False


def test_id_suffix_detection_only_marks_documented_free_models():
    from adapters import ModelInfo

    adapter = get_adapter("kilo-code")
    free = adapter.detect_free_from_api(ModelInfo("stepfun/model:free", "free", "text"))
    routed = adapter.detect_free_from_api(ModelInfo("kilo-auto/free", "router", "text"))
    paid = adapter.detect_free_from_api(ModelInfo("anthropic/paid", "paid", "text"))
    assert free["is_free"] is True
    assert routed["is_free"] is True
    assert paid["is_free"] is False


@pytest.mark.asyncio
@respx.mock
async def test_health_check_maps_success_and_rate_limit_headers():
    route = respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-rpd": "100",
                "x-ratelimit-remaining-rpd": "99",
            },
            json={"choices": [{"message": {"content": "OK"}}]},
        )
    )

    info = await _mistral_adapter().health_check(
        "mistral-small-latest", "secret", "https://api.mistral.ai/v1"
    )

    assert info.status == "healthy"
    assert info.error_code is None
    assert info.observed_rate_limit == {"rpd": 100}
    assert info.observed_remaining == {"rpd_remaining": 99}
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"
    assert b'"max_tokens":8' in route.calls[0].request.content


@pytest.mark.asyncio
@respx.mock
async def test_health_check_accepts_reasoning_only_success():
    adapter = get_adapter("kilo-code")
    respx.post("https://api.kilo.ai/api/gateway/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "", "reasoning": "OK"}}]},
        )
    )
    info = await adapter.health_check(
        "stepfun/step-3.7-flash:free", "", adapter.default_base_url
    )
    assert info.error_code is None
    assert info.status in {"healthy", "slow"}


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status_code", "expected_status", "error_code"),
    [
        (401, "down", "auth_failed"),
        (402, "down", "payment_required"),
        (404, "down", "not_found"),
        (429, "slow", "rate_limited"),
        (503, "slow", "server_error"),
    ],
)
async def test_health_check_maps_upstream_status(status_code, expected_status, error_code):
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=httpx.Response(status_code, json={"error": "test"})
    )
    info = await _mistral_adapter().health_check(
        "mistral-small-latest", "secret", "https://api.mistral.ai/v1"
    )
    assert info.status == expected_status
    assert info.error_code == error_code


def test_registry_exposes_declarative_setup_metadata():
    mistral = next(provider for provider in list_providers() if provider["id"] == "mistral")
    assert mistral["config_type"] == "declarative"
    assert mistral["requirements"]["requires_card"] is False
    assert mistral["setup"]["console_url"].startswith("https://")
    assert mistral["compliance"]["reviewed_at"] == "2026-07-21"

    kilo = next(provider for provider in list_providers() if provider["id"] == "kilo-code")
    assert kilo["setup"]["key_optional"] is True
    assert kilo["requirements"]["requires_card"] is False


def test_every_registered_provider_has_reviewed_compliance_metadata():
    providers = list_providers()
    assert len(providers) >= 6
    for provider in providers:
        compliance = provider.get("compliance")
        assert compliance, provider["id"]
        assert compliance["risk"] in {"low", "medium", "high", "unknown"}
        assert compliance["note"].strip()
        assert compliance["reviewed_at"]
        assert compliance["sources"]


class TestStaticModels:
    """static_models：目录端点为空的平台静态声明模型"""

    @staticmethod
    def _adapter(static_models=None):
        from adapters.declarative_config import DeclarativeProviderConfig
        from adapters.declarative import DeclarativeAdapter

        config = DeclarativeProviderConfig(
            id="test-static", name="Test", base_url="https://api.test",
            static_models=static_models or ["static-model"],
            free_detection={"method": "allowlist", "model_ids": ["static-model"]},
            setup={"description": "t", "key_hint": "t", "console_url": "https://c.test"},
            compliance={"note": "t", "reviewed_at": "2026-09-12", "sources": ["https://s.test"]},
        )
        return DeclarativeAdapter(config)

    @pytest.mark.asyncio
    async def test_static_model_added_when_catalog_empty(self):
        import httpx
        from unittest.mock import patch
        adapter = self._adapter()
        resp = httpx.Response(200, request=httpx.Request("GET", "https://api.test/models"),
                              json={"data": []})
        with patch.object(httpx.AsyncClient, "get", return_value=resp):
            models = await adapter.list_models("k", "https://api.test")
        ids = [m.model_id for m in models]
        assert "static-model" in ids

    @pytest.mark.asyncio
    async def test_static_model_not_duplicated_in_catalog(self):
        import json
        from unittest.mock import patch
        import httpx
        adapter = self._adapter()
        resp = httpx.Response(200, request=httpx.Request("GET", "https://api.test/models"),
                              json={"data": [{"id": "static-model"}]})
        with patch.object(httpx.AsyncClient, "get", return_value=resp):
            models = await adapter.list_models("k", "https://api.test")
        assert [m.model_id for m in models].count("static-model") == 1

    def test_duplicate_static_models_rejected(self):
        import pytest as _pytest
        from adapters.declarative_config import DeclarativeProviderConfig
        with _pytest.raises(Exception):
            DeclarativeProviderConfig(
                id="bad", name="Bad", base_url="https://api.test",
                static_models=["m1", "m1"],
                free_detection={"method": "allowlist", "model_ids": ["m1"]},
                setup={"description": "t", "key_hint": "t", "console_url": "https://c.test"},
                compliance={"note": "t", "reviewed_at": "2026-09-12", "sources": ["https://s.test"]},
            )
