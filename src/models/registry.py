"""
Model registry and router.

Bedrock is a model plane, not a Claude endpoint. This module is what makes
that true in the code: nodes ask for a TASK, the router picks a model, and
the client adapts to whatever that model can actually do.

Three routing policies:
  PINNED  — an explicit model key. User choice, or an eval run.
  TIER    — cheapest enabled model that meets the tier.
  AUTO    — per-task preference order from config, first enabled wins.

The catalogue lives in config/models.json rather than here, because model
ids and prices change faster than releases, and because under IaC that file
becomes an SSM Parameter the stack writes — letting an operator retune
routing without a deploy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get(
        "MODEL_CONFIG_PATH",
        str(Path(__file__).resolve().parents[2] / "config" / "models.json"),
    )
)


class ModelTier(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class RoutingPolicy(StrEnum):
    PINNED = "pinned"
    TIER = "tier"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """
    What a model can actually do.

    This is the point of the whole module. Llama does not support tool use;
    Nova does not support prompt caching. Code that assumes every model
    behaves like Claude breaks silently on the first non-Anthropic model —
    usually by returning prose where JSON was expected.
    """

    tool_use: bool
    system_prompt: bool
    prompt_caching: bool
    json_mode: bool


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    model_id: str
    display_name: str
    family: str
    tiers: tuple[ModelTier, ...]
    capabilities: Capabilities
    max_output_tokens: int
    cache_min_tokens: int
    input_cost_per_1k: Decimal
    output_cost_per_1k: Decimal
    enabled: bool

    def cost_for(self, input_tokens: int, output_tokens: int) -> Decimal:
        """Estimated USD. Used by the eval harness to compare models on price."""
        return (
            self.input_cost_per_1k * Decimal(input_tokens) / 1000
            + self.output_cost_per_1k * Decimal(output_tokens) / 1000
        ).quantize(Decimal("0.000001"))

    @property
    def is_configured(self) -> bool:
        """A model with no resolved id cannot be routed to."""
        return bool(self.model_id)


class UnroutableTask(RuntimeError):
    """No enabled, configured model can serve this task."""


class ModelRegistry:
    def __init__(self, config_path: Path | None = None) -> None:
        raw = json.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
        self.region: str = raw.get("region", "ap-southeast-2")
        self._routing: dict[str, dict] = raw.get("routing", {})
        self._specs: dict[str, ModelSpec] = {}

        for entry in raw["models"]:
            caps = entry["capabilities"]
            limits = entry["limits"]
            cost = entry["cost_per_1k"]
            # Model ids resolve from the environment first, falling back to
            # a default_model_id in the config. The env override exists because
            # Sydney often needs cross-region inference profile ids with an
            # 'apac.' or 'au.' prefix, and operators may need to switch without
            # a deploy. The default means the system works out-of-the-box once
            # the config lists valid ids for the account.
            model_id = os.environ.get(entry["model_id_env"], "") or entry.get(
                "default_model_id", ""
            )

            self._specs[entry["key"]] = ModelSpec(
                key=entry["key"],
                model_id=model_id,
                display_name=entry["display_name"],
                family=entry["family"],
                tiers=tuple(ModelTier(t) for t in entry["tiers"]),
                capabilities=Capabilities(
                    tool_use=caps["tool_use"],
                    system_prompt=caps["system_prompt"],
                    prompt_caching=caps["prompt_caching"],
                    json_mode=caps["json_mode"],
                ),
                max_output_tokens=limits["max_output_tokens"],
                cache_min_tokens=limits.get("cache_min_tokens", 0),
                input_cost_per_1k=Decimal(cost["input"]),
                output_cost_per_1k=Decimal(cost["output"]),
                enabled=entry.get("enabled", False),
            )

    # ------------------------------------------------------------ lookup

    def get(self, key: str) -> ModelSpec:
        if key not in self._specs:
            raise UnroutableTask(f"Unknown model key: {key}")
        return self._specs[key]

    def all_specs(self) -> list[ModelSpec]:
        return list(self._specs.values())

    def available(self, tier: ModelTier | None = None) -> list[ModelSpec]:
        """Enabled, configured, and matching the tier if one is given."""
        out = [s for s in self._specs.values() if s.enabled and s.is_configured]
        if tier is not None:
            out = [s for s in out if tier in s.tiers]
        return sorted(out, key=lambda s: s.input_cost_per_1k)

    # ------------------------------------------------------------ routing

    def route(
        self,
        task: str,
        *,
        policy: RoutingPolicy = RoutingPolicy.AUTO,
        pinned_key: str | None = None,
    ) -> ModelSpec:
        """
        Pick a model for a named task ('classify_intent', 'generate_plan', ...).

        Raises UnroutableTask rather than falling back to an arbitrary model.
        Silently substituting a weaker model would change output quality with
        no signal, which is worse than failing.
        """
        if policy is RoutingPolicy.PINNED:
            if not pinned_key:
                raise UnroutableTask("PINNED policy requires a model key")
            spec = self.get(pinned_key)
            if not spec.is_configured:
                raise UnroutableTask(
                    f"Model '{pinned_key}' has no id configured. "
                    f"Set the matching BEDROCK_MODEL_* variable."
                )
            return spec

        rule = self._routing.get(task)
        if rule is None:
            raise UnroutableTask(f"No routing rule for task '{task}'")
        tier = ModelTier(rule["tier"])

        if policy is RoutingPolicy.AUTO:
            for key in rule.get("prefer", []):
                spec = self._specs.get(key)
                if spec and spec.enabled and spec.is_configured and tier in spec.tiers:
                    return spec

        candidates = self.available(tier)
        if not candidates:
            raise UnroutableTask(
                f"No enabled model for task '{task}' at tier '{tier.value}'. "
                f"Enable one in config/models.json and set its model id."
            )
        return candidates[0]

    def explain_routing(self) -> str:
        """Diagnostic. Shows what each task would resolve to right now."""
        lines = []
        for task in self._routing:
            try:
                spec = self.route(task)
                lines.append(f"  {task:<20} -> {spec.display_name} ({spec.key})")
            except UnroutableTask as exc:
                lines.append(f"  {task:<20} -> UNROUTABLE: {exc}")
        return "\n".join(lines)
