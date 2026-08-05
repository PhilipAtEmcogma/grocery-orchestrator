"""
The model (LLM) boundary.

  base.py       ModelClient Protocol + ModelTier — what every graph node
                depends on. Start here.
  registry.py   Reads config/models.json and picks a concrete model per task
                (routing policy, capabilities, cost).
  bedrock.py    The real implementation, calling Bedrock's Converse API.
                Untested without an AWS account.
  scripted.py   A deterministic stand-in used by every test and by local dev
                (USE_BEDROCK unset). No network calls.

Nodes depend only on the ModelClient Protocol in base.py, never on boto3 or
a specific model id, so the graph can be built and tested with `scripted.py`
and later pointed at `bedrock.py` with no change to any node.
"""
