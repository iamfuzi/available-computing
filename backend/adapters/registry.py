from .base import ProviderAdapter
from .groq import GroqAdapter
from .siliconflow import SiliconFlowAdapter
from .gemini import GeminiAdapter
from .openrouter import OpenRouterAdapter

_registry: dict[str, ProviderAdapter] = {}


def _register(adapter: ProviderAdapter):
    _registry[adapter.provider_id] = adapter


_register(GroqAdapter())
_register(SiliconFlowAdapter())
_register(GeminiAdapter())
_register(OpenRouterAdapter())


def get_adapter(provider_id: str) -> ProviderAdapter:
    adapter = _registry.get(provider_id)
    if not adapter:
        raise ValueError(f"No adapter registered for provider: {provider_id}")
    return adapter


def list_providers() -> list[dict]:
    return [
        {"id": a.provider_id, "name": a.display_name, "base_url": a.default_base_url}
        for a in _registry.values()
    ]
