# AI quality rules

## Model selection is data, not code

Nodes request a TASK; the registry routes. Never hardcode a model id in a node.
The catalogue is `config/models.json`; under IaC it becomes an SSM Parameter.

## Never assume a model behaves like Claude

Branch on `ModelSpec.capabilities`:

- `tool_use: false` -> JSON-in-prose plus parsing for structured output
- `prompt_caching: false` -> omit the cache point
- below `cache_min_tokens` -> verify `cacheReadInputTokens`, never assume a hit

A model without tool use returning prose where JSON was expected is the first
failure a multi-model system hits.

## No prompt change without an eval run

Editing `src/prompts/` is unmeasured until `python evals/run_intent.py` runs.
Record accuracy before and after. A readable prompt that lowers the score is a
regression.

## Golden-set discipline

- `known_gap` cases are reported separately and never counted. Do not remove one
  to raise a score.
- Do not edit an expectation to match observed output. If the case is wrong,
  record why.
- Null expectations are correctness requirements; never invent a budget or
  other missing constraint.
- Assert what a value resolves to, not its brittle literal wording.
- Keep grounding, dietary, arithmetic, Guardrail, and honest-failure negative
  controls deterministic and version-controlled.

## Local and managed evaluations are complementary

Version-controlled local unit tests, golden sets, invariant evals, negative
controls, and CI floors remain release gates. Proposed Bedrock Model Evaluation
and AgentCore Evaluations add evidence for deployed model, Gateway, and Runtime
behavior; they do not replace local gates, relax the 90% task floor, qualify a
route on another task's score, or override a failed invariant.

Every managed run must be reproducible from committed or content-addressed
inputs and record: service/evaluator and model versions, resolved model id,
region or inference profile, date, prompt version, dataset revision and S3
object version, rubric and thresholds, pass rate, per-case outcomes, latency,
tokens/cache reads, estimated cost, and trace/run identifiers. Export results
to a versioned artefact with retention and deletion metadata. Never upload
shopper PII, credentials, or production prompts containing personal data.

Gateway parity evaluations must prove that the managed route invokes the same
coarse operation and returns the same contract-valid outcome class as the local
MCP/application path. Reviewer evaluations measure citation validity, schema
validity, false positives/negatives, cap enforcement, and human acceptance;
they never grant publication authority. Automated Reasoning, where supported,
is advisory evidence only and cannot replace deterministic checks.

## Enabling a model

A model with `enabled: true` must first be scored on the applicable golden set.
For pilot routing, every enabled model must score at least 90% on each task it
can actively serve. Models blocked on account/provider access stay disabled.
Never lower a floor or reorder routing to hide a failed score.

The scorecard records model key and resolved id, region/inference profile, date,
prompt version, case-set revision, pass rate, latency percentiles, token use,
cache reads, and estimated cost. A configuration change is incomplete until its
evidence changes with it. Cross-Region inference profiles follow the same rule;
the profile is routing infrastructure, not model qualification.
