import pytest
from sqlmodel import Session

from models import CandidateProvider, CandidateSourceState
from services import candidate_pool
from services.candidate_pool import ParsedCandidate, _admission_policy, parse_markdown_candidates


def test_parser_extracts_provider_base_url_and_models():
    markdown = """
## Free Providers

### [Example AI](https://example.com/keys)

Permanent free tier, no credit card required.

Base URL: `https://api.example.com/v1`

| Model Name | Context |
| --- | --- |
| example-7b | 32K |
| example-70b | 128K |

### [Second API](https://second.example)

- [model-one](https://second.example/model-one)

## Providers with trial credits

### [Trial Only](https://trial.example)

Base URL: `https://trial.example/v1`
"""
    candidates = parse_markdown_candidates(markdown, "fixture")
    assert [candidate.provider_id for candidate in candidates] == ["example-ai", "second-api"]
    assert candidates[0].base_url == "https://api.example.com/v1"
    assert candidates[0].models == ["example-7b", "example-70b"]
    assert candidates[1].models == ["model-one"]
    assert "trial" not in candidates[1].raw_section.lower()


def test_last_free_provider_section_stops_before_trial_heading():
    markdown = """
## Free Providers
### [Cloud Free](https://free.example)
Permanent free tier, no credit card.

## Providers with trial credits
### [Trial Only](https://trial.example)
Trial credits: $1.
"""
    candidates = parse_markdown_candidates(markdown, "fixture")
    assert len(candidates) == 1
    assert candidates[0].provider_id == "cloud-free"
    assert "trial credits" not in candidates[0].raw_section.lower()
    assert _admission_policy(candidates[0])[2] == "review_required"


def test_parser_normalizes_known_provider_aliases():
    markdown = """
## Free Providers
### [Google AI Studio](https://aistudio.google.com)
Free tier.
### [Z AI (Zhipu AI)](https://open.bigmodel.cn)
Permanent free models.
"""
    candidates = parse_markdown_candidates(markdown, "fixture")
    assert [candidate.provider_id for candidate in candidates] == ["google-gemini", "zhipu"]


def test_parser_merges_mistral_name_variants_to_registered_id():
    markdown = """
## Provider APIs
### [Mistral AI](https://console.mistral.ai)
Base URL: `https://api.mistral.ai/v1`
"""
    candidate = parse_markdown_candidates(markdown, "fixture")[0]
    assert candidate.provider_id == "mistral"


def test_parser_merges_codestral_candidate_into_mistral_provider():
    markdown = """
## Provider APIs
### [Mistral (Codestral)](https://codestral.mistral.ai/)
Free plan.
"""
    candidate = parse_markdown_candidates(markdown, "fixture")[0]
    assert candidate.provider_id == "mistral"


def test_parser_handles_html_model_tables():
    markdown = """
## Free Providers
### [Groq](https://console.groq.com)
<table><tbody>
<tr><td>llama-3.3-70b</td><td>1000 RPD</td></tr>
<tr><td>qwen3-32b</td><td>1000 RPD</td></tr>
</tbody></table>
"""
    candidate = parse_markdown_candidates(markdown, "fixture")[0]
    assert candidate.models == ["llama-3.3-70b", "qwen3-32b"]


def test_two_source_failures_raise_attention_without_deleting_candidates(
    test_engine, monkeypatch
):
    monkeypatch.setattr(candidate_pool, "engine", test_engine)
    with Session(test_engine) as session:
        session.add(CandidateProvider(
            provider_id="kept-provider",
            name="Kept",
            homepage_url="https://kept.example",
        ))
        session.commit()

    candidate_pool._mark_source_failure("fixture", "https://source.example", "parse failed")
    candidate_pool._mark_source_failure("fixture", "https://source.example", "parse failed again")

    with Session(test_engine) as session:
        state = session.get(CandidateSourceState, "fixture")
        assert state.consecutive_failures == 2
        assert state.needs_attention is True
        assert session.get(CandidateProvider, "kept-provider") is not None


def test_successful_source_resets_failure_state_and_persists_draft(
    test_engine, monkeypatch
):
    monkeypatch.setattr(candidate_pool, "engine", test_engine)
    candidate_pool._mark_source_failure("fixture", "https://source.example", "failed")
    parsed = ParsedCandidate(
        provider_id="example-ai",
        name="Example AI",
        homepage_url="https://example.com/keys",
        source_id="fixture",
        base_url="https://api.example.com/v1",
        models=["example-7b"],
    )
    candidate_pool._persist_source("fixture", "https://source.example", [parsed])

    with Session(test_engine) as session:
        state = session.get(CandidateSourceState, "fixture")
        row = session.get(CandidateProvider, "example-ai")
        assert state.consecutive_failures == 0
        assert state.needs_attention is False
        assert row.status == "pending"
        assert row.compatibility == "openai_compatible"
        assert "TODO: review official terms" in row.yaml_draft


def test_admission_policy_excludes_card_and_trial_candidates():
    card = ParsedCandidate(
        provider_id="card-provider",
        name="Card Provider",
        homepage_url="https://example.com",
        source_id="fixture",
        raw_section="Free trial. Credit card required.",
    )
    trial = ParsedCandidate(
        provider_id="cohere",
        name="Cohere",
        homepage_url="https://cohere.com",
        source_id="fixture",
        raw_section='Free "Trial" API key, no credit card.',
    )
    assert _admission_policy(card)[2] == "excluded"
    assert _admission_policy(card)[1] is True
    assert _admission_policy(trial)[0] == "trial_credit"
    assert _admission_policy(trial)[2] == "excluded"


def test_admission_policy_keeps_permanent_free_for_manual_review():
    candidate = ParsedCandidate(
        provider_id="permanent-provider",
        name="Permanent Provider",
        homepage_url="https://example.com",
        source_id="fixture",
        raw_section="Permanent free tier, no credit card required. 30 RPM.",
    )
    assert _admission_policy(candidate) == (
        "permanent_free", False, "review_required", None
    )


@pytest.mark.parametrize(
    "provider_id",
    [
        "aion-labs",
        "cloudflare",
        "github-models",
        "llm7-io",
        "modelscope",
        "ollama-cloud",
        "opencode-zen",
        "ovhcloud-ai-endpoints",
        "sambanova",
    ],
)
def test_official_review_excludes_disallowed_candidates(provider_id):
    candidate = ParsedCandidate(
        provider_id=provider_id,
        name=provider_id,
        homepage_url="https://example.com",
        source_id="fixture",
    )
    assert _admission_policy(candidate)[2] == "excluded"


def test_official_review_keeps_kilo_for_integration():
    candidate = ParsedCandidate(
        provider_id="kilo-code",
        name="Kilo Code",
        homepage_url="https://kilo.ai",
        source_id="fixture",
    )
    assert _admission_policy(candidate) == (
        "recurring_free",
        False,
        "review_required",
        None,
    )
