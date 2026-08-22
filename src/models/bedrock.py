"""
Bedrock-backed ModelClient.

UNTESTED until the AWS account lands — it cannot be exercised without
credentials. Everything above it is already proven by the scripted client, so
when the account arrives the only new surface is this file.

Model ids are resolved from environment variables rather than hardcoded,
because Sydney (ap-southeast-2) often requires cross-region inference
profiles, which carry an `apac.` prefix rather than the bare `anthropic.` one.
Confirm the correct ids with:

    aws bedrock list-foundation-models --region ap-southeast-2 \
        --query "modelSummaries[?contains(modelId,'claude')].[modelId,inferenceTypesSupported]"
"""

from __future__ import annotations

import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import ValidationError

from src.models.base import ModelClient, ModelError, ModelTier, T
from src.models.guardrail import guard_content_block
from src.models.registry import ModelRegistry, ModelSpec, RoutingPolicy

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")


def _guardrail_config() -> tuple[str, str, bool]:
    """
    Read at call time, not import time.

    Lambda can set environment after module import, and reading at call time
    is what makes the fail-closed behaviour testable without reloading the
    module. Returns (id, version, required).

    REQUIRE_GUARDRAIL defaults ON: opting out of content safety must be a
    deliberate, visible configuration choice, never the accidental result of
    forgetting to set an id.
    """
    return (
        os.environ.get("BEDROCK_GUARDRAIL_ID", ""),
        os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT"),
        os.environ.get("REQUIRE_GUARDRAIL", "1") == "1",
    )


class BedrockModelClient(ModelClient):
    def __init__(
        self,
        region: str = REGION,
        *,
        registry: ModelRegistry | None = None,
        pinned_spec: ModelSpec | None = None,
    ) -> None:
        # pinned_spec forces every call to one model. The eval harness uses
        # this to score models individually; production leaves it None and
        # lets the registry route per task.
        self._registry = registry or ModelRegistry()
        self._pinned = pinned_spec
        # Retries and timeouts matter: this sits inside a Lambda with a 29s
        # ceiling from API Gateway. Unbounded retries would blow through it.
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                retries={"max_attempts": 2, "mode": "standard"},
                read_timeout=20,
                connect_timeout=5,
            ),
        )
        self._usage: dict = {}

    def _spec_for(self, task: str) -> ModelSpec:
        if self._pinned is not None:
            return self._pinned
        return self._registry.route(task, policy=RoutingPolicy.AUTO)

    # ------------------------------------------------------------ interface

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "classify_intent",
    ) -> T:
        """
        Structured output, adapted to what the model can actually do.

        Tool use is preferred: forcing a tool call means the model cannot
        prepend "Sure, here's the JSON:" and break parsing. But not every
        model on Bedrock supports it — Llama does not — so a model without
        tool use gets the schema in the prompt and its reply parsed. That
        path is genuinely weaker, which is why the eval harness exists to
        measure the difference rather than assume it is fine.
        """
        spec = self._spec_for(task)
        if spec.capabilities.tool_use:
            return self._structured_via_tool_use(
                system=system, user=user, schema=schema, spec=spec,
                max_tokens=max_tokens,
            )
        return self._structured_via_prose(
            system=system, user=user, schema=schema, spec=spec,
            max_tokens=max_tokens,
        )

    def _structured_via_tool_use(
        self, *, system: str, user: str, schema: type[T],
        spec: ModelSpec, max_tokens: int,
    ) -> T:
        tool_name = schema.__name__
        raw = self._converse(
            system=system, user=user, spec=spec, max_tokens=max_tokens,
            tool_config={
                "tools": [{
                    "toolSpec": {
                        "name": tool_name,
                        "description": f"Return the result as a {tool_name}.",
                        "inputSchema": {"json": schema.model_json_schema()},
                    }
                }],
                "toolChoice": {"tool": {"name": tool_name}},
            },
        )

        for block in raw.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block:
                try:
                    return schema.model_validate(block["toolUse"]["input"])
                except ValidationError as exc:
                    raise ModelError(f"{tool_name} failed validation: {exc}") from exc

        raise ModelError(f"model returned no {tool_name} tool call")

    def _structured_via_prose(
        self, *, system: str, user: str, schema: type[T],
        spec: ModelSpec, max_tokens: int,
    ) -> T:
        """Fallback for models without tool use. Schema in prompt, parse reply."""
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        augmented_system = (
            f"{system}\n\n"
            f"Reply with a single JSON object matching this schema and nothing "
            f"else. No prose, no explanation, no markdown code fences.\n\n"
            f"{schema_json}"
        )
        raw = self._converse(
            system=augmented_system, user=user, spec=spec, max_tokens=max_tokens
        )
        text = "".join(
            b.get("text", "")
            for b in raw.get("output", {}).get("message", {}).get("content", [])
        )
        try:
            return schema.model_validate_json(_extract_json(text))
        except (ValidationError, ValueError) as exc:
            raise ModelError(
                f"{schema.__name__} could not be parsed from prose reply: {exc}"
            ) from exc

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "generate_prose",
    ) -> str:
        raw = self._converse(
            system=system, user=user, spec=self._spec_for(task), max_tokens=max_tokens
        )
        parts = [
            b["text"]
            for b in raw.get("output", {}).get("message", {}).get("content", [])
            if "text" in b
        ]
        if not parts:
            raise ModelError("model returned no text content")
        return "".join(parts)

    @property
    def last_usage(self) -> dict:
        return dict(self._usage)

    # ------------------------------------------------------------ internals

    def _converse(
        self,
        *,
        system: str,
        user: str,
        spec: ModelSpec,
        max_tokens: int,
        tool_config: dict | None = None,
    ) -> dict:
        model_id = spec.model_id
        if not model_id:
            raise ModelError(f"Model '{spec.key}' has no id configured.")

        kwargs: dict = {
            "modelId": model_id,
            "system": [{"text": system}],
            # The user turn is wrapped in a guardContent block. Without this
            # the PROMPT_ATTACK filter never evaluates anything — it has no way
            # to tell our instructions from the user's. The system prompt is
            # deliberately NOT wrapped, so our own instructions are not flagged.
            "messages": [{"role": "user", "content": [guard_content_block(user)]}],
            "inferenceConfig": {
                "maxTokens": min(max_tokens, spec.max_output_tokens),
                "temperature": 0.0,
            },
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config

        # Prompt caching. Only worth a cachePoint if the model supports it AND
        # the prefix is likely to clear the model minimum — below it the call
        # succeeds but nothing caches, so we would pay the write cost for
        # nothing. Verify real hits via cacheReadInputTokens, not by assuming.
        if spec.capabilities.prompt_caching:
            approx_tokens = len(system) // 4
            if approx_tokens >= spec.cache_min_tokens:
                kwargs["system"] = [{"text": system}, {"cachePoint": {"type": "default"}}]

        # Guardrails are opt-in and must be attached explicitly. A missing
        # guardrail id is a configuration error, not something to shrug at:
        # every generation call is required to pass through one.
        guardrail_id, guardrail_version, required = _guardrail_config()
        if guardrail_id:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": guardrail_id,
                "guardrailVersion": guardrail_version,
                # Required for guardContent blocks to be evaluated at all.
                "trace": "enabled",
            }
        elif required:
            # Fail closed. A missing guardrail is a misconfiguration, and
            # running generation without one is exactly the state this
            # control exists to prevent.
            raise ModelError(
                "BEDROCK_GUARDRAIL_ID is not set and REQUIRE_GUARDRAIL is on. "
                "Refusing to invoke a model without content safety."
            )

        started = time.perf_counter()
        try:
            response = self._client.converse(**kwargs)
        except ClientError as exc:
            raise ModelError(f"Bedrock call failed: {exc}") from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage = response.get("usage", {})
        self._usage = {
            "model_ids": [model_id],
            "model_key": spec.key,
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
            "cache_write_tokens": usage.get("cacheWriteInputTokens", 0),
            "latency_ms": elapsed_ms,
            "guardrail_intervened": response.get("stopReason") == "guardrail_intervened",
        }

        if response.get("stopReason") == "guardrail_intervened":
            raise GuardrailBlocked("Request blocked by Bedrock Guardrail")

        return response


class GuardrailBlocked(ModelError):
    """Raised when a Guardrail intervenes. Maps to ErrorCode.GUARDRAIL_BLOCKED."""


def describe_configuration() -> str:
    """Diagnostic for the smoke test. Reports what is set, never the values."""
    registry = ModelRegistry()
    guardrail_id, version, required = _guardrail_config()
    return json.dumps(
        {
            "region": REGION,
            "routing": {
                task: (
                    registry.route(task).display_name
                    if _safe_route(registry, task)
                    else "UNROUTABLE"
                )
                for task in ("classify_intent", "generate_plan", "repair_plan")
            },
            "guardrail": "configured" if guardrail_id else "UNSET",
            "guardrail_version": version if guardrail_id else None,
            "fail_closed": required,
        },
        indent=2,
    )


def _safe_route(registry: ModelRegistry, task: str) -> bool:
    try:
        registry.route(task)
    except Exception:
        return False
    return True


def _extract_json(text: str) -> str:
    """
    Pull a JSON object out of a prose reply.

    Models without tool use wrap JSON in markdown fences or preamble however
    firmly you instruct otherwise. Braces are matched rather than regexed so
    nested objects survive.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")

    depth = 0
    for i, ch in enumerate(cleaned[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    raise ValueError("unbalanced braces in reply")
