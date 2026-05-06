from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml
from config import WHITELIST_PATH


@dataclass
class WhitelistEntry:
    model_id: str
    free_type: str          # permanent / quota / grant
    rate_limit: Optional[dict] = None
    notes: Optional[str] = None
    category: Optional[str] = None


class WhitelistManager:
    def __init__(self):
        self._data: dict = {}
        self.version: str = ""
        self.load()

    def load(self, path: Optional[Path] = None):
        target = path or WHITELIST_PATH
        if not target.exists():
            return
        with open(target) as f:
            self._data = yaml.safe_load(f) or {}
        self.version = self._data.get("version", "unknown")

    def is_provider_all_free(self, provider_id: str) -> bool:
        provider = self._data.get("providers", {}).get(provider_id, {})
        return provider.get("free_strategy") == "all"

    def get_provider_free_type(self, provider_id: str) -> str:
        provider = self._data.get("providers", {}).get(provider_id, {})
        return provider.get("free_type", "permanent")

    def match(self, provider_id: str, model_id: str) -> Optional[WhitelistEntry]:
        provider = self._data.get("providers", {}).get(provider_id, {})
        for entry in provider.get("free_models", []):
            entry_id = entry.get("id", "")
            # Exact match or suffix match (e.g. "gemini-2.0-flash" matches "models/gemini-2.0-flash")
            if model_id == entry_id or model_id.endswith("/" + entry_id) or model_id.endswith(entry_id):
                return WhitelistEntry(
                    model_id=entry_id,
                    free_type=entry.get("free_type", "permanent"),
                    rate_limit=entry.get("rate_limit"),
                    notes=entry.get("notes"),
                    category=entry.get("category"),
                )
        return None


# Singleton
whitelist = WhitelistManager()
