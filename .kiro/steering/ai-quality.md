# AI quality rules

## Model selection is data, not code

Nodes request a TASK; the registry routes. Never hardcode a model id in a node.
The catalogue is `config/models.json`. Under IaC it becomes an SSM Parameter,
so routing can be retuned without a deploy.

## Never assume a model behaves like Claude

Code paths must branch on `ModelSpec.capabilities`:
- `tool_use: false` -> structured output falls back to JSON-in-prose + parse
- `prompt_caching: false` -> omit the cachePoint block
- Below `cache_min_tokens` -> the call succeeds but nothing caches; verify with
  `cacheReadInputTokens` in the response rather than assuming a hit

A model without tool use returning prose where JSON was expected is the first
failure a multi-model system hits.

## No prompt change without an eval run

Editing anything in `src/prompts/` is an unmeasured change until
`python evals/run_intent.py` has run. Record accuracy before and after in the
commit message. A prompt "improvement" that lowers the score is a regression
regardless of how it reads.

## Golden set discipline

- Cases marked `known_gap` are reported separately and never counted. Do NOT
  remove a gap case to raise the score.
- Do NOT edit an expectation to match observed model output. If the model is
  right and the case is wrong, say so explicitly in the commit message.
- Null expectations matter as much as positive ones. "Must not invent a budget"
  is a correctness requirement: a hallucinated constraint silently changes what
  the user asked for.
- Assert what a value RESOLVES to, not its literal string. Asserting the model
  returned exactly "butter" is brittle and tests the wrong thing.

## Enabling a model

A model in `config/models.json` with `enabled: true` must have been scored on
the golden set first. Ship the scorecard with the change.
