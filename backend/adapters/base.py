from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelInfo:
    model_id: str
    display_name: str
    category: str                   # text / vision / code / embedding
    context_length: Optional[int] = None
    rate_limit: Optional[dict] = None
    raw: dict = field(default_factory=dict)


@dataclass
class HealthInfo:
    status: str                     # healthy / slow / down
    response_ms: int
    error_code: Optional[str] = None
    observed_rate_limit: Optional[dict] = None     # parsed from response headers
    observed_remaining: Optional[dict] = None       # remaining quota from headers


class ProviderAdapter(ABC):

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @abstractmethod
    async def validate_key(self, key: str, base_url: str) -> None:
        """Raise an exception if the key is invalid."""

    @abstractmethod
    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]:
        """Return all models available under this key."""

    @abstractmethod
    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        """
        Inspect the API response fields to determine if a model is free.

        Returns one of:
        - {"is_free": True,  "free_type": ...}  — confirmed free
        - {"is_free": False, "free_type": ...}  — confirmed paid (e.g. by a
          naming convention such as SiliconFlow's "Pro/" prefix)
        - None                                   — unknown, defer to whitelist
        """

    async def fetch_free_model_ids(self, key: str, base_url: str) -> Optional[set[str]]:
        """
        Fetch the authoritative set of currently-free model ids from the
        provider's API (e.g. a dedicated "free models" endpoint).

        Returns the set of free model ids, or None if the provider does not
        expose such an endpoint (in which case detection falls back to
        detect_free_from_api / whitelist). The default implementation returns
        None so adapters without a free-listing endpoint are unaffected.
        """
        return None

    @abstractmethod
    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        """Send a minimal probe request and return the health result."""
