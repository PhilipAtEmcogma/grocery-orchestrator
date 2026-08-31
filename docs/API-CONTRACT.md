# API contract — see [`CONTRACT-v1.md`](../CONTRACT-v1.md)

**[`CONTRACT-v1.md`](../CONTRACT-v1.md) in the repository root is the contract.**
It is what `src/schemas/contract.py` implements, what `validate.py` checks on
every CI run, and what the samples in `samples/` are generated from.

There is one contract document, and this is not it.

---

## What this file used to be, and why it is now a pointer

Until 2026-08-31 this file held a 180-line contract of its own, written on the
`frontend-infra-setup` branch on 2026-08-21. It described a different service:

| | this document said | the service does |
|---|---|---|
| `location` | required `string` | optional object with `lat`/`lon`/`region` |
| prices | numeric (`3.49`) | **strings** (`"3.49"`) |
| `turn_id` | absent | **required** |
| response | one flat object with a `type` | `{version, session_id, turn_id, events[]}` |

A client written from it returns **HTTP 400**. The teammate's own client works
precisely because it does not follow it.

**Two contract documents in one repository is the failure mode**, not a
duplication problem. This repository's rule is that when two documents disagree
you check the thing they describe — and the thing they describe answers to
`CONTRACT-v1.md`. Keeping both, even with a warning banner on one, leaves a
reader one wrong click from building against a spec the service refuses.

**Nothing was thrown away.** The full field-by-field comparison, the six
questions this document raised, and the answers to them are in
[`OPEN-REVIEW-frontend-contract.md`](OPEN-REVIEW-frontend-contract.md) — which
is the right home for them, because they are a conversation rather than a
specification. The original text remains in git history at `9a09d87`.

## If you are building a client

- **The contract:** [`CONTRACT-v1.md`](../CONTRACT-v1.md) — request and response
  shapes, every event type, and the guarantees.
- **How to consume it:** [`FRONTEND-INTEGRATION.md`](../FRONTEND-INTEGRATION.md)
  — worked examples, the event loop, and the cases that surprise people
  (`no_data` and `notice` arrive *alongside* results, not instead of them).
- **Real payloads:** [`samples/`](../samples/) — regenerated from the running
  server and gated in CI, so they cannot drift from what you will actually
  receive.
- **Run it locally:** `python scripts/dev_server.py` serves the same contract on
  `localhost:8000`, with no AWS account.
