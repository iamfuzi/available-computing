from .base import ProviderAdapter
from .groq import GroqAdapter
from .siliconflow import SiliconFlowAdapter
from .openrouter import OpenRouterAdapter
from .zhipu import ZhiPuAdapter
from .agnes import AgnesAdapter
from .declarative import DeclarativeAdapter
from .declarative_config import load_declarative_providers
from .provider_metadata import CUSTOM_PROVIDER_METADATA
from config import PROVIDERS_PATH

_registry: dict[str, ProviderAdapter] = {}


def _register(adapter: ProviderAdapter):
    _registry[adapter.provider_id] = adapter


_register(GroqAdapter())
_register(SiliconFlowAdapter())
_register(OpenRouterAdapter())
_register(ZhiPuAdapter())
_register(AgnesAdapter())

for _config in load_declarative_providers(PROVIDERS_PATH):
    if _config.id in _registry:
        raise ValueError(f"declarative provider shadows built-in adapter: {_config.id}")
    _register(DeclarativeAdapter(_config))


def get_adapter(provider_id: str) -> ProviderAdapter:
    adapter = _registry.get(provider_id)
    if not adapter:
        raise ValueError(f"No adapter registered for provider: {provider_id}")
    return adapter


def list_providers() -> list[dict]:
    providers = []
    for adapter in _registry.values():
        item = {
            "id": adapter.provider_id,
            "name": adapter.display_name,
            "base_url": adapter.default_base_url,
            "config_type": "custom",
        }
        if isinstance(adapter, DeclarativeAdapter):
            item.update({
                "config_type": "declarative",
                "requirements": adapter.config.requirements.model_dump(),
                "setup": adapter.config.setup.model_dump(),
                "compliance": adapter.config.compliance.model_dump(),
            })
        else:
            item.update(CUSTOM_PROVIDER_METADATA.get(adapter.provider_id, {}))
        providers.append(item)
    return providers
