# Open review — should ADR 0002 be approved, and how much of it?

**Status:** open, and wants a mentor.
**Raised:** 2026-08-31 · **Effort:** about twenty minutes · **You need no code.**

Companion to [`OPEN-REVIEW-head-terms.md`](OPEN-REVIEW-head-terms.md) and
[`OPEN-REVIEW-min-grams-per-person-day.md`](OPEN-REVIEW-min-grams-per-person-day.md),
and deliberately the same shape: a decision the build cannot make for itself,
written up so the person who should make it can.

The ADR itself is
[`adr/0002-staged-agentcore-and-managed-ai-services.md`](adr/0002-staged-agentcore-and-managed-ai-services.md).
This page exists because that document is 231 lines of scope and evidence
criteria, and the decision in front of you is smaller than it looks.

---

## The question, in one line

**May we build three AgentCore-shaped things that sit beside the deterministic
service, none of which is allowed to touch the shopper's answer?**

## What approving it does NOT do

Worth saying first, because it is the usual reason an ADR like this stalls.

- It **does not move the shopper path onto AgentCore.** That is a separate
  decision with its own trigger: measured p99 above ~25 seconds after
  mitigations. Current meal-plan p95 is 11.7–12.2s, so it is not close.
- It **does not authorise deployment.** Approving the ADR clears gate 1 of
  seven. Gates 3–7 — threat model and IAM, versioned acceptance data, CDK
  definition, WAF/Cognito, and a successful teardown drill — still bind before
  anything is created in the account.
- It **does not weaken any invariant.** No price may originate from model
  generation; honest failure beats a plausible answer; dietary exclusions fail
  closed. Nothing proposed here can publish a price or write to production.

What it does is unblock the *building*, which is currently the reason three
tasks sit unstarted.

## The three things

| | What it is | What it would touch |
|---|---|---|
| **Gateway** | A managed auth/policy/mediation layer in front of the same two coarse MCP operations that already exist | Nothing new — it fronts the operations, it never calls into LangGraph |
| **Reviewer Runtime** | An isolated agent that reads a capped, sanitised catalogue snapshot and reports suspected data errors | Read-only, no shopper data, no writes, findings go to a person |
| **Managed evaluations** | Bedrock Model Evaluation / AgentCore Evaluations as *companion* evidence | Reporting only; the local scorecards stay the release gates |

## Where each one actually stands

This is the part that changed since the ADR was written on 2026-08-23, and the
reason it is worth reading again now.

**Gateway — its precondition is met.** Gate 2 says the local MCP contract must
be proven before Gateway work. It is: Pilot Task 8 shipped a stdio MCP server
exposing two coarse operations that invoke the whole application service. No raw
AWS, data, filesystem, network, write, citation, or generation primitive is
exposed. Gateway would front exactly those operations.

**Reviewer — half of it is already built, deliberately.** `src/review/` holds
the sanitised snapshot boundary and the deterministic validation that a
reviewer's findings must survive. That half needed no approval, because it is
required *whoever* reviews — including a person with a spreadsheet, which is
what we fall back to if you decline. See `ARCHITECTURE.md` §3n. What is missing
is the Runtime, the isolated identity, and the caps.

**Evaluations — no precondition met yet.** Gate 4 wants versioned acceptance
datasets and negative controls before any evaluation work, and the labelled
anomaly dataset for the reviewer does not exist.

## What the reviewer can and cannot do, concretely

The reviewer is the only proposal here that involves a model looking at our
data, so it is the one worth checking carefully. It is already constrained by
code that exists today:

- It sees **13 named fields** and nothing else. Not a redacted `PriceRecord` —
  a separate type built from an allowlist, so a field added to retrieval later
  cannot silently reach it. No shopper messages, locations, dietary data,
  sessions or credentials, because there is no field for them.
- It sees **at most 500 rows**, and asking for more **raises** rather than
  quietly truncating.
- Every finding it produces is checked three ways before a human ever sees it:
  the row it cites must be in the snapshot it was given, the values it quotes
  must match that row exactly, and it may report but not prescribe. A finding
  that fails any of these is recorded as a fabrication and dropped.
- There is **no field for a proposed value**, and prose that proposes one
  ("should be $2.49") is refused too.

The reason for that last rule is a defect this project already had: a citation
naming the right table, with a plausible key and a price nobody retrieved,
passed cleanly. **Shape is not identity.** A finding is the same risk wearing
different clothes.

## What it would cost

August spend was **$17.63** against a $25/month budget, and $10.61 of that was
live evaluation sessions rather than serving — actual serving is about $4.70.

The reviewer runs over a capped snapshot on demand, not per shopper request, so
its cost is bounded by how often we run it rather than by traffic. The honest
statement is that we do not yet have a measured figure, because nothing has run.
Gate 3 requires a cost budget before deployment, and the existing budget alarm
notifies at 50/80/100%.

## The case against approving

Set out properly, because the easy failure here is a mentor approving a
document nobody argued with.

1. **Service count is not a product outcome.** The ADR says this itself. A
   shopper gets nothing from Gateway that they do not get today.
2. **The reviewer's value is unproven.** The one anomaly we know about — a
   `unit_price_nzd` of "2490.00" against a $2.49 broccoli — is caught by six
   lines of deterministic code. The reviewer's case rests entirely on anomalies
   nobody thought to write a rule for, and that is a hypothesis.
3. **Three more surfaces is three more things to keep secure and paid for**, in
   a project whose frontend is not built and whose CDK cutover is outstanding.
4. **It is reversible, which cuts both ways.** Easy rollback is a good property
   and also the argument that always gets used to say yes.

## What we are asking for

A choice, not a signature. Any of these is a usable answer:

- **Approve all three.** Building starts against gates 3–7.
- **Approve the reviewer Runtime only.** The most learning per dollar, and the
  half that is already boxed in by code.
- **Approve Gateway only.** The one whose precondition is fully met.
- **Approve none.** Then the reviewer's deterministic half becomes a human
  workflow, Task 14 closes as 14a, and Tasks 8-extension and the evaluation
  companions close as not-pursued. Nothing else in the project changes.

**A decline costs us very little**, which is the point of having built the
boundary before the thing that sits behind it.

---

## Where to record the answer

Update **Status** at the top of
[`adr/0002-staged-agentcore-and-managed-ai-services.md`](adr/0002-staged-agentcore-and-managed-ai-services.md),
with the date and which components are covered. If any component is declined,
say so there explicitly rather than by omission — the tasks that depend on it
read that line.
