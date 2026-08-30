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
    # These tests exercise routing and capabilities, not content safety.
    # A guardrail id is set so the fail-closed check does not mask them;
    # the fail-closed behaviour itself is tested in test_guardrail.py.
    monkeypatch.setenv("BEDROCK_GUARDRAIL_ID", "test-guardrail")


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
    monkeypatch.delenv("BEDROCK_MODEL_LLAMA", raising=False)
    fresh = ModelRegistry()
    with pytest.raises(UnroutableTask):
        fresh.route("classify_intent", policy=RoutingPolicy.PINNED, pinned_key="llama-instruct")


def test_disabled_models_are_not_routed_to(registry):
    """A disabled model must not be selected regardless of tier or preference."""
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
    "query_items": ["butter"],
    "dietary_exclusions": [],
    "preferred_stores": [],
}


def test_tool_use_model_gets_a_tool_config(monkeypatch, registry):
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
        },
    )

    result = client.structured(
        system="sys",
        user="cheapest butter",
        schema=IntentResult,
        tier=ModelTier.FAST,
    )
    assert result.query_items == ["butter"]
    assert "toolConfig" in stub.kwargs


def test_model_without_tool_use_falls_back_to_prose(monkeypatch, registry):
    """Llama has no tool use. Schema goes in the prompt; reply gets parsed."""
    spec = registry.get("llama-instruct")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"text": "```json\n" + json.dumps(_INTENT_PAYLOAD) + "\n```"}]
                }
            },
            "usage": {"inputTokens": 300, "outputTokens": 40},
        },
    )

    result = client.structured(
        system="sys",
        user="cheapest butter",
        schema=IntentResult,
        tier=ModelTier.FAST,
    )
    assert result.query_items == ["butter"]
    assert "toolConfig" not in stub.kwargs
    assert "schema" in stub.kwargs["system"][0]["text"].lower()


def test_prose_fallback_survives_preamble(monkeypatch, registry):
    """Models ignore 'no preamble' instructions. Parsing must cope."""
    spec = registry.get("llama-instruct")
    client, _ = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": "Sure! Here is the JSON you asked for:\n"
                            + json.dumps(_INTENT_PAYLOAD)
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 300, "outputTokens": 40},
        },
    )

    result = client.structured(system="sys", user="x", schema=IntentResult, tier=ModelTier.FAST)
    assert result.intent.value == "price_check"


def test_unparseable_prose_raises_rather_than_returning_partial(monkeypatch, registry):
    spec = registry.get("llama-instruct")
    client, _ = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {"message": {"content": [{"text": "I'd rather not."}]}},
            "usage": {"inputTokens": 300, "outputTokens": 10},
        },
    )

    with pytest.raises(ModelError):
        client.structured(system="sys", user="x", schema=IntentResult, tier=ModelTier.FAST)


def test_cache_point_omitted_for_models_without_caching(monkeypatch, registry):
    spec = registry.get("nova-lite")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
        },
    )

    client.structured(system="s" * 40000, user="x", schema=IntentResult, tier=ModelTier.FAST)
    blocks = stub.kwargs["system"]
    assert not any("cachePoint" in b for b in blocks)


def test_cache_point_omitted_below_the_model_minimum(monkeypatch, registry):
    """Below cache_min_tokens the call succeeds but caches nothing."""
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
        },
    )

    client.structured(system="short prompt", user="x", schema=IntentResult, tier=ModelTier.FAST)
    assert not any("cachePoint" in b for b in stub.kwargs["system"])


def test_cache_point_added_for_large_prompts_on_capable_models(monkeypatch, registry):
    spec = registry.get("claude-haiku")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}]
                }
            },
            "usage": {"inputTokens": 5000, "outputTokens": 20, "cacheReadInputTokens": 4200},
        },
    )

    client.structured(system="s" * 40000, user="x", schema=IntentResult, tier=ModelTier.FAST)
    assert any("cachePoint" in b for b in stub.kwargs["system"])
    assert client.last_usage["cache_read_tokens"] == 4200


def test_max_tokens_clamped_to_model_limit(monkeypatch, registry):
    """Nova's ceiling is lower than Claude's. Asking for more is an API error."""
    spec = registry.get("nova-lite")
    client, stub = _stub_client(
        monkeypatch,
        spec,
        {
            "output": {
                "message": {
                    "content": [{"toolUse": {"name": "IntentResult", "input": _INTENT_PAYLOAD}}]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
        },
    )

    client.structured(
        system="s", user="x", schema=IntentResult, tier=ModelTier.FAST, max_tokens=100000
    )
    assert stub.kwargs["inferenceConfig"]["maxTokens"] <= spec.max_output_tokens


# ============================================== Pilot Task 7: route qualification
#
# `enabled` used to mean "listed in the config", not "has evidence". Every model
# carried enabled=true regardless of what it had been scored on, and
# claude-sonnet was second preference for generate_plan while being documented
# as excluded on LATENCY -- p50 11.8s / p90 19.9s against the production 20s
# client timeout, 9 of 98 plan calls over the ceiling. A Nova Pro outage failed
# over to it.
#
# Worse than the preference list suggests: route() falls through to
# available(tier) sorted by cost when no preferred model is eligible, and
# claude-sonnet declared BOTH the quality and fast tiers, so it was a live
# fallback candidate for every task in the graph.


def test_no_routable_model_serves_a_task_it_was_never_scored_on():
    """
    The gate. Adding a model, enabling one, or adding a task fails the build
    until someone records a scorecard or names the gap deliberately.
    """
    registry = ModelRegistry()
    unscored = registry.unscored_routes()
    assert unscored == [], (
        "these (task, model) pairs are reachable with no qualifying evidence: "
        f"{unscored}. Record a scorecard in config/models.json, disable the "
        "model, or name the task in scorecards._unscored_tasks with a reason."
    )


def test_no_enabled_model_is_wholly_unevidenced():
    """
    Complements the above, which skips tasks nobody evaluates.

    Nothing measures prose, so that exemption would otherwise let a model with
    zero evidence anywhere serve every prose turn. Unscored for an unmeasured
    task is acceptable; unscored everywhere and still routable is not.
    """
    unevidenced = ModelRegistry().unevidenced_models()
    assert unevidenced == [], f"enabled with no qualifying scorecard on any task: {unevidenced}"


def test_the_unmeasured_tasks_are_named_and_reasoned():
    """
    An accepted gap must be a decision on the record, not an omission. If a task
    disappears from this list without gaining a scorecard, the gate above starts
    failing -- which is the intended direction.
    """
    gaps = ModelRegistry().unscored_tasks()
    # generate_prose left this list on 2026-08-29 when evals/run_prose.py gave
    # it a scorecard. repair_plan is still here, but for a different reason than
    # it used to be: it IS measured now (evals/run_repair.py), and is ungated
    # only because six cases cannot support a threshold.
    assert set(gaps) == {"repair_plan"}
    for task, reason in gaps.items():
        assert len(reason) > 80, f"{task} needs a real reason, not a label"


def test_a_model_excluded_on_latency_is_not_a_silent_fallback():
    """
    Regression guard for the specific defect. claude-sonnet must not be
    reachable for any task while it has no scorecard.
    """
    registry = ModelRegistry()
    for task in ("classify_intent", "generate_plan", "repair_plan", "generate_prose"):
        assert "claude-sonnet" not in registry.routable_models(task), (
            f"claude-sonnet is reachable for {task} despite having no scorecard"
        )


# ---------------------------------------------------------------- exclusion


def test_an_excluded_model_is_not_routed_and_not_reported_as_routable(tmp_path):
    """
    Per-task scoring implies per-task exclusion, and the config could not say it.

    A model can clear the floor on one task and fall below it on another.
    `available(tier)` would still hand it the second task as a cost-ordered
    fallback, so `enabled: false` was the only lever -- and that removes the
    model everywhere, including from tasks it is good at. `claude-sonnet` only
    fitted that lever because it was unfit for everything.
    """
    import json

    config = {
        "version": "test",
        "region": "ap-southeast-2",
        "models": [
            {
                "key": "cheap-but-bad-at-repair",
                "model_id_env": "BEDROCK_MODEL_NOVA_LITE",
                "default_model_id": "test.cheap-but-bad-at-repair",
                "display_name": "Cheap",
                "family": "amazon",
                "tiers": ["fast"],
                "capabilities": {
                    "tool_use": True,
                    "system_prompt": True,
                    "prompt_caching": False,
                    "json_mode": True,
                },
                "limits": {"max_output_tokens": 4096, "cache_min_tokens": 4096},
                "cost_per_1k": {"input": "0.00001", "output": "0.00001"},
                "enabled": True,
            },
            {
                "key": "good-at-repair",
                "model_id_env": "BEDROCK_MODEL_NOVA_PRO",
                "default_model_id": "test.good-at-repair",
                "display_name": "Good",
                "family": "amazon",
                "tiers": ["fast"],
                "capabilities": {
                    "tool_use": True,
                    "system_prompt": True,
                    "prompt_caching": False,
                    "json_mode": True,
                },
                "limits": {"max_output_tokens": 4096, "cache_min_tokens": 4096},
                "cost_per_1k": {"input": "0.001", "output": "0.001"},
                "enabled": True,
            },
        ],
        "routing": {
            "repair_plan": {
                "tier": "fast",
                "prefer": ["good-at-repair"],
                "exclude": ["cheap-but-bad-at-repair"],
            }
        },
        "scorecards": {"_floor": 0.9},
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    registry = ModelRegistry(path)

    # Not reachable by preference, and not by the cost-ordered fallback either.
    assert "cheap-but-bad-at-repair" not in registry.routable_models("repair_plan")
    assert registry.route("repair_plan").key == "good-at-repair"


def test_exclusion_is_honoured_by_route_and_routable_models_alike(tmp_path):
    """
    The two must agree, or the gate reports a route no turn can reach.

    `routable_models` answers "what could a turn reach"; if it disagreed with
    `route`, the build would fail on a pair that does not exist -- and a gate
    that cries wolf gets switched off.
    """
    import json

    config = {
        "version": "test",
        "region": "ap-southeast-2",
        "models": [
            {
                "key": "only-model",
                "model_id_env": "BEDROCK_MODEL_NOVA_LITE",
                "default_model_id": "test.only-model",
                "display_name": "Only",
                "family": "amazon",
                "tiers": ["fast"],
                "capabilities": {
                    "tool_use": True,
                    "system_prompt": True,
                    "prompt_caching": False,
                    "json_mode": True,
                },
                "limits": {"max_output_tokens": 4096, "cache_min_tokens": 4096},
                "cost_per_1k": {"input": "0.00001", "output": "0.00001"},
                "enabled": True,
            }
        ],
        "routing": {"repair_plan": {"tier": "fast", "prefer": [], "exclude": ["only-model"]}},
        "scorecards": {"_floor": 0.9},
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    registry = ModelRegistry(path)

    assert registry.routable_models("repair_plan") == []
    with pytest.raises(UnroutableTask):
        registry.route("repair_plan")
