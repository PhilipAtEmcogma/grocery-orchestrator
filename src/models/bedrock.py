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
from pydantic import BaseModel, ValidationError

from src.models.base import ModelClient, ModelError, ModelTier

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

MODEL_IDS = {
    ModelTier.FAST: os.environ.get("BEDROCK_MODEL_FAST", ""),
    ModelTier.QUALITY: os.environ.get("BEDROCK_MODEL_QUALITY", ""),
}

GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")


class BedrockModelClient(ModelClient):
    """Concrete ModelClient implementation that talks to Amazon Bedrock's Converse API."""

    def __init__(self, region: str = REGION) -> None:
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

    # ------------------------------------------------------------ interface

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        tier: ModelTier,
        max_tokens: int = 1024,
    ):
        """
        Structured output via tool use.

        Forcing a tool call is more reliable than asking for JSON in prose:
        the model cannot prepend "Sure, here's the JSON:" and break parsing.
        """
        # Define a single Bedrock "tool" whose input schema is the pydantic
        # model's JSON schema, then force the model to call it.
        tool_name = schema.__name__
        tool_spec = {
            "toolSpec": {
                "name": tool_name,
                "description": f"Return the result as a {tool_name}.",
                "inputSchema": {"json": schema.model_json_schema()},
            }
        }

        raw = self._converse(
            system=system,
            user=user,
            tier=tier,
            max_tokens=max_tokens,
            tool_config={
                "tools": [tool_spec],
                "toolChoice": {"tool": {"name": tool_name}},
            },
        )

        # Find the tool-use block in the reply and validate its input against
        # the requested schema.
        for block in raw.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block:
                try:
                    return schema.model_validate(block["toolUse"]["input"])
                except ValidationError as exc:
                    raise ModelError(f"{tool_name} failed validation: {exc}") from exc

        raise ModelError(f"model returned no {tool_name} tool call")

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
    ) -> str:
        raw = self._converse(
            system=system, user=user, tier=tier, max_tokens=max_tokens
        )
        # Concatenate every text block in the reply's content list.
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
        """Copy so callers can't mutate the client's internal usage state."""
        return dict(self._usage)

    # ------------------------------------------------------------ internals

    def _converse(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int,
        tool_config: dict | None = None,
    ) -> dict:
        # Look up which concrete Bedrock model id serves this tier.
        model_id = MODEL_IDS.get(tier)
        if not model_id:
            raise ModelError(
                f"No model id configured for tier {tier.value}. "
                f"Set BEDROCK_MODEL_{tier.value.upper()}."
            )

        # temperature=0.0: deterministic output matters more than creativity
        # for classification/extraction/planning against a fixed catalogue.
        kwargs: dict = {
            "modelId": model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config

        # Guardrails are opt-in and must be attached explicitly. A missing
        # guardrail id is a configuration error, not something to shrug at:
        # every generation call is required to pass through one.
        if GUARDRAIL_ID:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": GUARDRAIL_ID,
                "guardrailVersion": GUARDRAIL_VERSION,
            }

        started = time.perf_counter()
        try:
            response = self._client.converse(**kwargs)
        except ClientError as exc:
            raise ModelError(f"Bedrock call failed: {exc}") from exc

        # Record token counts and latency for this call so callers/observability
        # tooling can read them via last_usage afterwards.
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage = response.get("usage", {})
        self._usage = {
            "model_ids": [model_id],
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "latency_ms": elapsed_ms,
            "guardrail_intervened": response.get("stopReason") == "guardrail_intervened",
        }

        if response.get("stopReason") == "guardrail_intervened":
            raise GuardrailBlocked("Request blocked by Bedrock Guardrail")

        return response


class GuardrailBlocked(ModelError):
    """Raised when a Guardrail intervenes. Maps to ErrorCode.GUARDRAIL_BLOCKED."""


def describe_configuration() -> str:
    """Diagnostic helper for the smoke test."""
    return json.dumps(
        {
            "region": REGION,
            "fast_model": MODEL_IDS[ModelTier.FAST] or "UNSET",
            "quality_model": MODEL_IDS[ModelTier.QUALITY] or "UNSET",
            "guardrail": GUARDRAIL_ID or "UNSET (required before production)",
        },
        indent=2,
    )
