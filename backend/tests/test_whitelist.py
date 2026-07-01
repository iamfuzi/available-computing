import pytest
import yaml
from pathlib import Path
from services.whitelist import WhitelistManager, WhitelistEntry


@pytest.fixture
def whitelist_yaml(tmp_path):
    """Create a temp whitelist YAML file."""
    data = {
        "version": "test-1.0",
        "providers": {
            "groq": {
                "free_strategy": "all",
            },
            "siliconflow": {
                "free_models": [
                    {"id": "Qwen/Qwen2.5-7B-Instruct", "free_type": "permanent"},
                    {"id": "deepseek-ai/DeepSeek-V2.5", "free_type": "quota", "rate_limit": {"rpm": 10}},
                ],
            },
            "gemini": {
                "free_models": [
                    {"id": "gemini-2.5-flash", "free_type": "permanent", "category": "text"},
                    {"id": "gemini-2.5-pro", "free_type": "permanent", "rate_limit": {"rpd": 50}},
                    {"id": "gemini-3-pro-preview", "free_type": "permanent", "param_size": 600},
                ],
            },
        },
    }
    path = tmp_path / "providers.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def wl(whitelist_yaml):
    """WhitelistManager loaded from the temp YAML."""
    mgr = WhitelistManager()
    mgr.load(whitelist_yaml)
    return mgr


class TestIsProviderAllFree:
    def test_all_free(self, wl):
        assert wl.is_provider_all_free("groq") is True

    def test_not_all_free(self, wl):
        assert wl.is_provider_all_free("siliconflow") is False

    def test_unknown_provider(self, wl):
        assert wl.is_provider_all_free("unknown") is False


class TestMatch:
    def test_exact_match(self, wl):
        entry = wl.match("siliconflow", "Qwen/Qwen2.5-7B-Instruct")
        assert entry is not None
        assert entry.model_id == "Qwen/Qwen2.5-7B-Instruct"
        assert entry.free_type == "permanent"

    def test_suffix_match(self, wl):
        """Model IDs like 'models/gemini-2.5-flash' should match."""
        entry = wl.match("gemini", "models/gemini-2.5-flash")
        assert entry is not None
        assert entry.model_id == "gemini-2.5-flash"

    def test_no_match(self, wl):
        assert wl.match("siliconflow", "nonexistent-model") is None

    def test_no_match_wrong_provider(self, wl):
        assert wl.match("groq", "Qwen/Qwen2.5-7B-Instruct") is None

    def test_rate_limit_preserved(self, wl):
        entry = wl.match("siliconflow", "deepseek-ai/DeepSeek-V2.5")
        assert entry.rate_limit == {"rpm": 10}

    def test_category_preserved(self, wl):
        entry = wl.match("gemini", "gemini-2.5-flash")
        assert entry.category == "text"

    def test_param_size_preserved(self, wl):
        # Closed-source ids with no parseable marker get param_size from the
        # whitelist — this is the fallback for the auto:smart router.
        entry = wl.match("gemini", "gemini-3-pro-preview")
        assert entry.param_size == 600

    def test_param_size_defaults_none(self, wl):
        entry = wl.match("gemini", "gemini-2.5-flash")
        assert entry.param_size is None


class TestGetProviderFreeType:
    def test_default_permanent(self, wl):
        assert wl.get_provider_free_type("groq") == "permanent"

    def test_unknown_provider(self, wl):
        assert wl.get_provider_free_type("nonexistent") == "permanent"


class TestLoad:
    def test_load_nonexistent_file(self, tmp_path):
        mgr = WhitelistManager.__new__(WhitelistManager)
        mgr._data = {}
        mgr.version = ""
        mgr.load(tmp_path / "does-not-exist.yaml")
        assert mgr.version == ""

    def test_version_loaded(self, wl):
        assert wl.version == "test-1.0"
