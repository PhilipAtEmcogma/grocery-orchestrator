# Open review: should the endpoints require an API key?

**Status:** designed, deliberately NOT applied. Needs a decision from a person.
**Raised:** 2026-08-31, closing the second audit's Finding 3 halfway.
**Audience:** anyone who can decide who holds a key. No code reading required.

---

## 1. The situation, in four facts

1. There are **two** public REST APIs in `ap-southeast-2`, both reachable by
   anyone who learns the URL, both invoking Bedrock on every request:
   `woqmel35lk` (hand-made, the one serving) and `crm1xkrk34` (CDK, deployed
   beside it while the cutover is deferred).
2. Neither requires authentication of any kind. This was the first audit's top
   security finding and the second audit's Finding 3, which observed that the
   deferred cutover had **doubled** it rather than changed it.
3. Both have a usage plan with throttling — 5 requests/second, burst 10 — so the
   spend from a single abuser is bounded per second. It is not bounded per day,
   and throttling is not attribution: a plan that throttles everyone equally
   cannot tell a shopper from a script.
4. As of this change **both planes are alarmed**, including the API 5xx alarm
   that previously watched only one (`infra/lib/observability-stack.ts`). So
   abuse is now visible. It is still not bounded.

## 2. What an API key would and would not buy

**Would.** Attribution and a per-caller quota. API Gateway usage plans support
a daily/weekly/monthly request quota *per key*, which is the control that turns
"someone is hammering the endpoint" from an unbounded Bedrock bill into a
number chosen in advance. It also makes the frontend's traffic distinguishable
from everything else, which is what makes a rate limit meaningful.

**Would not.** It is not authentication of a *user*. An API key shipped in a
browser client is readable by anyone who opens the network tab; it identifies
the APPLICATION, not the person. That is a real and useful thing — it is the
difference between one bounded consumer and an open door — but it must not be
described as auth, and `security.md` should not be read as satisfied by it.

**What actually authenticates a user** is Cognito or a JWT authorizer, which is
gated behind the identity work this project has deliberately not started
(`tasks.md`, and the ADR 0002 withdrawal of AgentCore Gateway). WAF is the third
layer and is about volume and shape rather than identity.

## 3. Why it is designed and not applied

Applying it is minutes of CDK. What it costs is not the CDK.

- **It changes the published contract.** `CONTRACT-v1.md` describes an
  unauthenticated `POST /chat`. Requiring a key adds a required `x-api-key`
  header, and a request without one gets HTTP 403 with API Gateway's error
  body — *not* the contract-valid `ChatResponse` this service guarantees on
  every other path, because the rejection happens before our handler runs.
  That is a real change to what the frontend must handle.
- **It breaks a client that already exists.** The branch
  `frontend-infra-setup` carries a working Vite/React client that has been
  building against this contract since 2026-08-21. Requiring a key without
  telling them is the kind of change that gets discovered as an outage.
- **Nobody has agreed who holds it.** A key in a static site's JavaScript is
  public. A key held server-side means a proxy, which means a backend the
  frontend team does not currently have. Those are different projects.

None of those is an engineering problem, which is exactly why this is a review
document and not a commit.

## 4. The design, if the answer is yes

```ts
// ServiceStack, after the usage plan:
const key = api.addApiKey('FrontendKey', { apiKeyName: `${n.restApi}${cfg.suffix}-frontend` });
plan.addApiKey(key);
plan.addApiStage({ stage: api.deploymentStage });

// And the quota that is the actual control:
//   quota: { limit: 20_000, period: apigateway.Period.MONTH }
// A number chosen in advance beats an alarm read afterwards.

chat.addMethod('POST', integration, { apiKeyRequired: true });
// OPTIONS stays apiKeyRequired: false. A browser preflight does not carry
// custom headers, so requiring a key on OPTIONS breaks CORS for everyone --
// and the handler answers OPTIONS itself, which is why there is no MOCK
// integration to configure instead.
```

Three details that are easy to get wrong and expensive to find:

- **`OPTIONS` must not require the key.** See above. This is the one that
  silently breaks every browser client.
- **The key value must not enter `config/` or the repository.** It is a secret
  and this repo is public. `tests/test_config_placeholders.py` already fails the
  build on a literal account id; a key belongs in the same category.
- **A key with no quota is decoration.** `apiKeyRequired: true` alone changes
  who can call, not how much. The quota is the control.

## 5. The three options, and what each costs

| | What it does | Cost |
|---|---|---|
| **A. Key on both planes** | Closes the class. Bounded, attributable spend on everything public | Contract change, frontend change, a custody decision |
| **B. Key on the CDK plane only** | Bounds the copy nobody consumes yet; the plane actually serving stays open | Half a control. The audit's finding is about the plane serving |
| **C. Alarms only** (what was done) | Abuse is visible and attributable to a time, not to a caller | The identity gap stays exactly where audit 1 left it |

**C is what this change implements**, on the reasoning that a control which
breaks a working client without anyone having agreed to it is not obviously
better than a visible gap. It is a holding position and should be recorded as
one — the gap is real, it is now doubled, and monitoring is not a bound.

## 6. What would change the answer

- **A date for the frontend cutover.** If the client is going to be repointed at
  the CDK plane anyway, the key can land in the same change and cost nothing
  extra in coordination.
- **Any traffic at all from outside the team.** Today the endpoint is unknown
  and unused, which is a reason to wait and not a reason to be comfortable.
  `docs/ARCHITECTURE.md` §3 has the URLs; anyone who reads this repository has
  them.
- **A demo outside the team.** The provenance question already gates that
  (`README.md` open questions); this should gate it too.
- **A Bedrock bill that moves.** The $25 budget in `ObservabilityStack` is the
  tripwire. If it fires and nobody on the team caused it, the answer is A, that
  day.

## 7. The question, in one line

**Who holds the key, and are we willing to change `CONTRACT-v1.md` and a
teammate's client to introduce it?** If the answer to the first half is "the
frontend, in a public bundle", say so explicitly — it is still worth doing for
the quota, and it should be recorded as bounding cost rather than as
authentication.
