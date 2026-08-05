"""
Multi-model tests.

Bedrock is a model plane, not a Claude endpoint. These verify the code
actually behaves that way — routing, capability branching, and the prose
fallback for models without tool use.

The Bedrock client is exercised with a stubbed `converse`, so no AWS.
"""

from __future__ import annotations

import json

import pytest

from src.models.base import ModelError, ModelTier
from src.models.registry import ModelRegistry, RoutingPolicy, UnroutableTask
from src.prompts.intent import IntentResult


@pytest.fixture(autouse=True)
def _model_ids(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_CLAUDE_HAIKU", "apac.test.haiku")
    monkeypatch.setenv("BEDROCK_MODEL_CLAUDE_SONNET", "apac.test.sonnet")
    monkeypatch.setenv("BEDROCK_MODEL_NOVA_LITE", "apac.test.nova-lite")
    monkeypatch.setenv("BEDROCK_MODEL_LLAMA", "apac.test.llama")


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


# ------------------------------------------------------------- routing


def test_tasks_route_to_their_configured_tier(registry):
    assert ModelTier.FAST in registry.route("classify_intent").tiers
    assert ModelTier.QUALITY in registry.route("generate_plan").tiers


def test_repair_uses_a_fast_model_not_the_expensive_one(registry):
    """Repair is substitution, not planning. Routing it to QUALITY is a cost bug."""
    assert ModelTier.FAST in registry.route("repair_plan").tiers


def test_unknown_task_raises_rather_than_guessing(registry):
    with pytest.raises(UnroutableTask):
        registry.route("summarise_the_news")


def test_pinning_an_unconfigured_model_raises(registry, monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_NOVA_LITE", raising=False)
    fresh = ModelRegistry()
    with pytest.raises(UnroutableTask):
        fresh.route("classify_intent", policy=RoutingPolicy.PINNED,
                    pinned_key="nova-lite")


def test_disabled_models_are_not_routed_to(registry):
    """nova-lite ships disabled; it must not be selected until scored."""
    for task in ("classify_intent", "generate_plan", "repair_plan"):
        assert registry.route(task).enabled


# ------------------------------------------------------------- capabilities


def test_capabilities_differ_across_families(registry):
    assert registry.get("claude-haiku").capabilities.tool_use is True
    assert registry.get("llama-instruct").capabilities.tool_use is False
    assert registry.get("nova-lite").capabilities.prompt_caching is False


def test_cost_estimates_are_family_specific(registry):
    haiku = registry.get("claude-haiku")
    sonnet = registry.get("claude-sonnet")
    assert haiku.cost_for(1000, 500) < sonnet.cost_for(1000, 500)


# ------------------------------------------------------------- client paths


def _stub_client(monkeypatch, spec, response: dict):
    from src.models.bedrock import BedrockModelClient

    client = BedrockModelClient.__new__(BedrockModelClient)
    client._registry = ModelRegistry()
    client._pinned = spec
    client._usage = {}

    class _Stub:
        """Records the kwargs the client passed to converse()."""

        def __init__(self) -> None:
            self.kwargs: dict = {}

        def converse(self, **kwargs) -> dict:
            self.kwargs = kwargs
            return response

    stub = _Stub()
    client._client = stub
    return client, stub


_INTENT_PAYLOAD = {
    "intent": "price_check",
    "confidence": 0.95,
    "query_item": "butter",
    "dietary_exclusions": [],
    "preferred_stores": [],
}


def test_tool_use_model_gets_a_tool_config(monkeypatch, registry):
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}
        ]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    })

    result = client.structured(
        system="sys", user="cheapest butter", schema=IntentResult,
        tier=ModelTier.FAST,
    )
    assert result.query_item == "butter"
    assert "toolConfig" in stub.kwargs


def test_model_without_tool_use_falls_back_to_prose(monkeypatch, registry):
    """Llama has no tool use. Schema goes in the prompt; reply gets parsed."""
    spec = registry.get("llama-instruct")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"text": "```json\n" + json.dumps(_INTENT_PAYLOAD) + "\n```"}
        ]}},
        "usage": {"inputTokens": 300, "outputTokens": 40},
    })

    result = client.structured(
        system="sys", user="cheapest butter", schema=IntentResult,
        tier=ModelTier.FAST,
    )
    assert result.query_item == "butter"
    assert "toolConfig" not in stub.kwargs
    assert "schema" in stub.kwargs["system"][0]["text"].lower()


def test_prose_fallback_survives_preamble(monkeypatch, registry):
    """Models ignore 'no preamble' instructions. Parsing must cope."""
    spec = registry.get("llama-instruct")
    client, _ = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"text": "Sure! Here is the JSON you asked for:\n"
                     + json.dumps(_INTENT_PAYLOAD)}
        ]}},
        "usage": {"inputTokens": 300, "outputTokens": 40},
    })

    result = client.structured(
        system="sys", user="x", schema=IntentResult, tier=ModelTier.FAST
    )
    assert result.intent.value == "price_check"


def test_unparseable_prose_raises_rather_than_returning_partial(monkeypatch, registry):
    spec = registry.get("llama-instruct")
    client, _ = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [{"text": "I'd rather not."}]}},
        "usage": {"inputTokens": 300, "outputTokens": 10},
    })

    with pytest.raises(ModelError):
        client.structured(
            system="sys", user="x", schema=IntentResult, tier=ModelTier.FAST
        )


def test_cache_point_omitted_for_models_without_caching(monkeypatch, registry):
    spec = registry.get("nova-lite")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}
        ]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    })

    client.structured(system="s" * 40000, user="x", schema=IntentResult,
                      tier=ModelTier.FAST)
    blocks = stub.kwargs["system"]
    assert not any("cachePoint" in b for b in blocks)


def test_cache_point_omitted_below_the_model_minimum(monkeypatch, registry):
    """Below cache_min_tokens the call succeeds but caches nothing."""
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}
        ]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    })

    client.structured(system="short prompt", user="x", schema=IntentResult,
                      tier=ModelTier.FAST)
    assert not any("cachePoint" in b for b in stub.kwargs["system"])


def test_cache_point_added_for_large_prompts_on_capable_models(monkeypatch, registry):
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}
        ]}},
        "usage": {"inputTokens": 5000, "outputTokens": 20,
                  "cacheReadInputTokens": 4200},
    })

    client.structured(system="s" * 40000, user="x", schema=IntentResult,
                      tier=ModelTier.FAST)
    assert any("cachePoint" in b for b in stub.kwargs["system"])
    assert client.last_usage["cache_read_tokens"] == 4200


def test_max_tokens_clamped_to_model_limit(monkeypatch, registry):
    """Nova's ceiling is lower than Claude's. Asking for more is an API error."""
    spec = registry.get("nova-lite")
    client, stub = _stub_client(monkeypatch, spec, {
        "output": {"message": {"content": [
            {"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}
        ]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    })

    client.structured(system="s", user="x", schema=IntentResult,
                      tier=ModelTier.FAST, max_tokens=100000)
    assert stub.kwargs["inferenceConfig"]["maxTokens"] <= spec.max_output_tokens
