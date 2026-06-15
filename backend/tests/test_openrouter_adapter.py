import pytest
from adapters.openrouter import OpenRouterAdapter
from adapters.base import ModelInfo


@pytest.fixture
def adapter():
    return OpenRouterAdapter()


def _make(raw_pricing):
    """Build a ModelInfo carrying only the given pricing dict in raw."""
    return ModelInfo(
        model_id="m",
        display_name="m",
        category="text",
        context_length=1000,
        raw=raw_pricing,
    )


class TestDetectFreeFromApi:
    """OpenRouter free detection keys off pricing.prompt / pricing.completion
    both being zero. The API has been observed returning these values as
    strings ("0"), ints (0), and floats (0.0); all must be recognised as
    free, while non-zero / malformed values must not raise."""

    @pytest.mark.parametrize("value", ["0", 0, 0.0, "0.0"])
    def test_free_when_both_prices_zero(self, adapter, value):
        m = _make({"pricing": {"prompt": value, "completion": value}})
        assert adapter.detect_free_from_api(m) == {
            "is_free": True,
            "free_type": "permanent",
        }

    @pytest.mark.parametrize(
        "prompt, completion",
        [
            ("0.0000001", "0.0000002"),  # tiny but non-zero
            ("1", "0"),
            ("0", "1"),
            (0.0000001, 0),
        ],
    )
    def test_paid_when_any_price_nonzero(self, adapter, prompt, completion):
        m = _make({"pricing": {"prompt": prompt, "completion": completion}})
        assert adapter.detect_free_from_api(m) == {"is_free": False}

    @pytest.mark.parametrize("raw", [{"pricing": {}}, {}])
    def test_paid_when_pricing_missing(self, adapter, raw):
        # Missing pricing fields default to 1 (treated as paid, not free).
        m = _make(raw)
        assert adapter.detect_free_from_api(m) == {"is_free": False}

    @pytest.mark.parametrize("value", ["free!", None, [0], {"x": 0}])
    def test_none_when_pricing_malformed(self, adapter, value):
        # Non-numeric values can't be parsed → defer to the whitelist
        # (None) rather than guessing.
        m = _make({"pricing": {"prompt": value, "completion": value}})
        assert adapter.detect_free_from_api(m) is None
