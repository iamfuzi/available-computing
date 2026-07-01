import pytest
from services.param_size import parse_param_size


class TestParseDense:
    """Single-count ids: the number before a bare ``b`` is the size."""

    @pytest.mark.parametrize("model_id, expected", [
        ("qwen2.5-72b", 72.0),
        ("Qwen/Qwen2.5-72B-Instruct", 72.0),
        ("gemma-4-31b", 31.0),
        ("ibm-granite/granite-4.1-8b", 8.0),
        ("meta-llama/Meta-Llama-3.1-70B-Instruct", 70.0),
    ])
    def test_dense_count(self, model_id, expected):
        assert parse_param_size(model_id) == expected

    def test_decimal_count(self):
        # Qwen's "0.5b" line
        assert parse_param_size("Qwen/Qwen2.5-0.5B-Instruct") == 0.5

    def test_strips_free_suffix(self):
        assert parse_param_size("google/gemma-4-31b-it:free") == 31.0


class TestParseMoE:
    """MoE ids carry two counts (total-activated); the activated one wins."""

    @pytest.mark.parametrize("model_id, expected", [
        ("nvidia/nemotron-3-ultra-550b-a55b", 55.0),
        ("qwen/qwen3.6-35b-a3b", 3.0),
        ("google/gemma-4-26b-a4b-it", 4.0),
        ("nvidia/nemotron-3-super-120b-a12b:free", 12.0),
    ])
    def test_moe_uses_activated(self, model_id, expected):
        assert parse_param_size(model_id) == expected

    def test_moe_activated_not_total(self):
        # 550b total but only 55b activated — must return 55, not 550.
        result = parse_param_size("nemotron-3-ultra-550b-a55b")
        assert result == 55.0
        assert result != 550.0


class TestParseUnparseable:
    """Closed-source / markerless ids return None (defer to whitelist)."""

    @pytest.mark.parametrize("model_id", [
        "gpt-4o",
        "claude-3.5-sonnet",
        "glm-4-flash",
        "gemini-2.5-pro",
        "z-ai/glm-5.2",
        "",
    ])
    def test_returns_none(self, model_id):
        assert parse_param_size(model_id) is None
