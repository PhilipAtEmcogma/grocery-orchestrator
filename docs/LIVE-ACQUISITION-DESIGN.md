# Live price acquisition — design, architecture, and reasoning record

**Status:** PARKED BY DESIGN. This is a design document, not an implementation
and not a decision to implement. Task 11.4 (live price acquisition) remains
**gated on all thirteen conditions in [`ACQUISITION-RISK.md`](../ACQUISITION-RISK.md) §8**,
and the recommended first step is **written permission from the retailers, not
engineering** (§7 of that assessment). Nothing here unblocks that gate; this
document exists so that when the gate clears, the build is a known quantity
rather than a fresh start.

**Why it is parked and written anyway.** The earlier agreement was to take the
design first and make it detailed enough to pick up cleanly later. This is that
document. It is deliberately buildable-from — every condition maps to a named
control in a named place — but the first action it recommends is to send an
email, because a "yes" from Foodstuffs or Woolworths NZ dissolves most of the
engineering below (§7 of the risk assessment), and a "no" is the answer to
whether 11.4 should happen at all.

**Read first:** [`ACQUISITION-RISK.md`](../ACQUISITION-RISK.md) — the terms-of-service
and legal risk assessment this design implements. Every §8 condition is
referenced by number below. Do not read this document as a substitute for that
one; read it as the engineering that that one's §8 requires.

---

## 1. What this is, in one paragraph

A design for the **live `PriceSource`** that would sit behind the existing
`ingestion/sources.py` seam — fetching current offers from Pak'nSave, New World
and Woolworths NZ by traversing their **published product sitemaps** (never the
robots-disallowed search endpoints), returning the same facts-only `RawOffer`
the fixture and lineage-B sources already return, and feeding the same
`refresh()` → validate → diff → write path that is already built and tested.
The acquisition itself is the only new part; everything downstream of the
`RawOffer` already exists.

---

## 2. The seam it plugs into (what already exists)

The offline structure — Task 7.5, unblocked and built — is the reason this
design is small. It is all in `ingestion/`:

- **`PriceSource` protocol** (`ingestion/sources.py`): one method, `fetch() ->
  list[RawOffer]`, plus a `retailer` property. A live source is a third
  implementation of this protocol alongside `FixtureSource` and
  `LineageBSource`. **Nothing downstream changes.**
- **`RawOffer`** (frozen dataclass): the facts-only shape — `product_key`,
  `store`, `store_location`, `display_name`, `canonical_name`, `category`,
  `price_nzd` (`Decimal`), `unit`, `pack_grams`, `on_special`, `captured_at`,
  `lat`, `lon`. `captured_at` is required and has no default (a price the
  shopper cannot date is a price they cannot evaluate — §8 condition 9).
- **`resolve_source(retailer)`**: the tripwire. It **raises `NotImplementedError`
  if `LIVE_ACQUISITION=1`**, before any config is consulted. That check is where
  a live source would be wired in, and it is deliberately a line that says why it
  is there — adding live acquisition means editing it, which is a reviewable act,
  not a config toggle.
- **`refresh()`** (`ingestion/handler.py`): fetch one retailer → `to_item()` →
  `reject_implausible()` → `diff_items()` → write to products + append to
  history. Per-retailer invocation (one failure domain each, Req 8.5). A live
  source produces `RawOffer`s and everything from `to_item` onward is unchanged
  and already guards the write.

**The design principle, stated once:** the live adapter's ONLY job is to turn a
retailer's published catalogue into `list[RawOffer]`. It does not decide what is
implausible (`reject_implausible` does), what changed (`diff_items` does), what
is stale (the freshness gate does), or what the shopper sees (the graph does).
It is a fetcher, and the narrower it is, the less of the risk surface it touches.

---

## 3. The route to building this at all (permission first)

`ACQUISITION-RISK.md` §7 is unambiguous and this design does not second-guess
it: **the recommended route to Task 11.4 is written permission, not careful
scraping.** The sequence is therefore:

1. **Send the email first.** A non-commercial workshop project approaching
   Foodstuffs and Woolworths NZ has, per §5, a materially better chance in this
   market than most — the regulator has already found against the retailers'
   market conduct, and the direction of travel is toward price transparency.
   Permission dissolves risk items 1–4 of §6 at once and costs nothing.
2. **Complete §8 condition 1** regardless: a human opens the three sources that
   failed automated fetch (the two Woolworths URLs and the Pak'nSave online-shop
   terms) in a browser and completes the §2 table. Until then every unknown is
   treated as prohibitive.
3. **Only then** spend engineering effort on the controls below — and if
   permission is granted, several of them (robots discipline, the
   permission-conditional gating) become simpler or moot.

This ordering is itself a control: it stops the project spending a fortnight on
sitemap parsers for a source it may never be allowed to use, and it means the
first artefact is the cheapest one that could settle the question.

---

## 4. Architecture

### 4.1 Where the adapter sits

```text
  EventBridge (daily)  ->  Step Functions Inline Map  ->  ingestion Lambda x3
                                                            (one per retailer)
                                                              |
                                       resolve_source(retailer)
                                                              |
                              LIVE_ACQUISITION set + permitted?
                                    |                        |
                                   no                       yes
                                    |                        |
                       FixtureSource / LineageBSource   LiveRetailerSource   <-- THIS DESIGN
                                    |                        |
                                    `----------> RawOffer[] <-'
                                                  |
                          to_item -> reject_implausible -> diff_items
                                                  |
                                    products table + append-only history
```

The live source is one box. Everything to its right is built, tested, and
unchanged. Everything to its left (the schedule, the Inline Map, the
per-retailer fan-out) is the ingestion architecture that already exists.

### 4.2 Inside the live adapter (per retailer)

A `LiveRetailerSource` is composed of four stages, each independently testable
against recorded fixtures:

```text
  1. robots gate      re-fetch robots.txt, parse, confirm the sitemap path is
                      allowed and the search paths are not requested (cond 2)
  2. sitemap walk     fetch the published product sitemap(s), enumerate product
                      URLs (cond 3) -- NEVER the search endpoint
  3. product fetch    fetch each product page, rate-limited with backoff
                      (cond 6), extracting FACTS ONLY (cond 7)
  4. normalise        map the retailer's shape to RawOffer, stamping
                      captured_at at fetch time (cond 9), canonicalising the
                      product key the same way the fixtures already are
```

Each stage is a pure function of its input plus an injected HTTP client, so the
whole adapter runs in tests against recorded responses with no network — the
same discipline `FixtureSource` uses and the same reason the rest of the system
is testable with no AWS.

### 4.3 The HTTP client is injected and recorded

The adapter takes an HTTP client as a constructor dependency (the
`PriceRepository`/`ModelClient` pattern). Two implementations:

- **`RecordedHttpClient`** — replays saved responses from `fixtures/acquisition/`
  (a VCR-style cassette per retailer). This is what tests and CI use; it sends
  no traffic and is the only client that exists until the gate clears.
- **`LiveHttpClient`** — the real fetcher, wrapping `urllib`/`httpx` with the
  rate limiting, backoff, timeout, and `User-Agent` the conditions require. It
  is the ONLY code in the project that makes an outbound request to a retailer,
  which is what makes the risk surface auditable: one file, one client.

Recording the cassettes is a deliberate, human-run, one-off step — a maintainer
with permission fetches a small sample, saves the responses, and commits them.
The cassettes are the recorded-response half of §7.5's "build against fixtures
and recorded responses".

---

## 5. Every §8 condition, mapped to a control

This is the core of the document: `ACQUISITION-RISK.md` §8 lists thirteen
conditions, and a live acquisition is not "started" until every one holds. Each
maps to a named engineering control and where it lives.

### Access conditions

| # | Condition | Control | Where |
|---|---|---|---|
| 1 | Three unretrieved sources read by a human; §2 table completed | **Manual, pre-build.** Not code. A checklist item in the PR that would add the live source; the source refuses to exist until it is ticked. | PR gate + `ACQUISITION-RISK.md` §2 |
| 2 | `robots.txt` honoured, **re-fetched each run**, search paths never requested | `robots gate` stage: fetch `robots.txt` at the start of every `fetch()`, parse with a real robots parser, assert the sitemap path is `Allow` and every candidate URL is not under a `Disallow`. A disallowed URL is skipped, not fetched. Transcribing the rules into code once is explicitly forbidden. | `LiveRetailerSource.fetch()`, stage 1 |
| 3 | Catalogue traversal uses the **published sitemaps**, not search | `sitemap walk` stage enumerates from `/sitemap*.xml` product sitemaps only. The search endpoint is never constructed as a URL anywhere in the adapter — there is no code path that could call it. | stage 2 |
| 4 | **No technical control circumvented, ever.** Not bot mitigation, not rate limits, not undocumented endpoints | **A block is a terminal answer, not an obstacle.** On any 403/429/CAPTCHA/connection-reset signal, the adapter STOPS for that retailer and reports it — it never retries harder, rotates a UA, changes IP, or seeks an alternate endpoint. This is the §4.2 criminal bright line and it is not a judgement call. Encoded as: the backoff has a hard failure ceiling, and the failure path is "stop and report", with no branch that escalates. | `LiveHttpClient` + stage 1/3 error handling |
| 5 | Descriptive `User-Agent` with a contact address | A constant `User-Agent` string identifying the project and a contact email, set on `LiveHttpClient` and non-overridable. Anonymity is not available to a defensible collector. | `LiveHttpClient` |
| 6 | Conservative rate limiting, backoff on error, hard stop on repeated failure; daily refresh not continuous crawl | A token-bucket or fixed-delay limiter well below anything that could degrade service; exponential backoff on transient errors; a hard stop after N consecutive failures. The schedule stays the existing **daily** EventBridge rule, never a continuous loop. | `LiveHttpClient` + the existing schedule |

### Storage conditions

| # | Condition | Control | Where |
|---|---|---|---|
| 7 | **Facts only** — key, name, size, retailer, `Decimal` price, capture date. No images, marketing copy, descriptions, reviews, or personal info | `RawOffer` **already has no field** for any of those. The adapter extracts only the fields `RawOffer` declares; there is nowhere to put an image or a description even by mistake. This is the allowlist discipline the review snapshot uses, applied at the source. | `RawOffer` (exists) |
| 8 | Store the **subset** needed to answer queries, not a mirror | The adapter fetches the catalogue but `refresh()` writes one row per (store, product); there is no "archive the whole page" path. Combined with cond 7, what persists is the factual subset, not a catalogue copy — which keeps the §4.3 compilation-copyright risk small. | `refresh()` (exists) |

### Output conditions — the §4.5 Fair Trading Act controls (the ones that matter most)

These are the binding constraint (§6 item 1), and the reassuring part of the
assessment is that they are **the existing shopper-path invariants applied to a
new data source** — not new discipline.

| # | Condition | Control | Where (already exists) |
|---|---|---|---|
| 9 | Capture date **surfaced to the user**, not merely stored | `RawOffer.captured_at` → `valid_date` on the record → already carried through retrieval into the response. The contract already exposes it; the frontend must display it. | `RawOffer.captured_at`, `valid_date` field, contract |
| 10 | Prices older than a staleness threshold are **not presented as current** — they take `no_data` | The freshness gate (`config/freshness.json`, `max_price_age_days`) already routes stale prices to the `STALE_DATA`/`no_data` path (Req 4.1–4.2). Live acquisition changes the DATA, not this control. **Return `max_price_age_days` to 14** (it is a dated 45-day dev stopgap) the day real ingested prices land — see `config/freshness.json` `_decision_2026_08_30`. | `src/retrieval/filters.py`, `config/freshness.json` |
| 11 | Store-level scope stated; a non-store-specific price must not imply it is | `RawOffer` carries `store` + `store_location`, and the record is keyed by `store_key`. A price is attributed to the store it was captured from. If a captured price is chain-wide rather than store-specific, the adapter must set `store_location` to reflect that rather than inventing a store. | `RawOffer.store_location`, `store_key` |
| 12 | Conditional pricing (club, loyalty, multi-buy, promo) represented faithfully or excluded — never flattened into a headline price | `on_special` is a boolean fact. Anything the adapter cannot represent faithfully as a plain price + `on_special` flag, it **excludes** rather than flattening. A multi-buy ("3 for $5") is not a unit price and must not be stored as one. This is a normalisation rule in stage 4, and the safe default is to drop the offer. | stage 4 normalise + `RawOffer` |
| 13 | No superlative claim beyond what the retrieved data supports at the recorded capture time | The prose node already forbids model-authored money and the grounding checks already require every price to cite a retrieved record. "Cheapest" is computed in code over retrieved records with their capture dates, never asserted by the model. Live data flows through the same checks. | `assert_grounded`, `assert_citations_match_retrieval`, prose node |

**The pattern in conditions 9–13:** every one is already enforced for the
fixture/lineage-B data. Live acquisition does not add a compliance mechanism; it
raises the cost of ever removing one. That is exactly what the risk assessment
says (§4.5: "Live acquisition does not introduce a new discipline; it raises the
cost of abandoning the one already in place").

---

## 6. Per-retailer specifics

### 6.1 Foodstuffs (Pak'nSave, New World) — the buildable ones

Per §3.1, the Foodstuffs sites have **no automated-access prohibition** in their
website terms, publish product sitemaps, leave product pages crawlable, and
disallow only the search paths. The sanctioned traversal is therefore:

- Enumerate product URLs from the published product sitemaps.
- Fetch product pages, extract facts only.
- Never construct a `/search`, `/Search`, `/shop/search`, or `/shop/Search` URL.

Two retailers, one Foodstuffs shape — likely one adapter parameterised by
retailer, since Pak'nSave and New World share the Foodstuffs platform. Confirm
the sitemap structure per banner when the cassettes are recorded.

### 6.2 Woolworths NZ — gated harder, treated as prohibited until confirmed

Per §3.2, Woolworths NZ is the **highest-risk** of the three: terms not
retrievable, apparent bot mitigation (the connection reset is the signature),
and an Australian parent with an explicit anti-scraping clause. The design
treats it as **prohibited** until §8 condition 1 confirms otherwise:

- The Woolworths adapter is **not built** in the first increment. Foodstuffs
  first, Woolworths only after a human has read its terms and `robots.txt` and
  (ideally) permission is granted.
- If bot mitigation is encountered at any point, condition 4 applies without
  exception: **stop, do not work around it.** This is the one path in the whole
  assessment with criminal exposure attached.

---

## 7. Security, IAM, network, secrets

- **Egress is the new surface.** The live source is the only component that
  makes outbound requests to a third party. It runs in the ingestion Lambda,
  which today needs no internet access; a live source needs egress, and that
  egress should be **allowlisted to the retailer domains** (via a VPC + NAT with
  an egress filter, or at minimum documented and monitored) so the Lambda cannot
  make arbitrary outbound requests. This is a new IAM/networking task, not a
  reuse.
- **No credentials, by design.** §1 of the risk assessment holds only while no
  account or API key is used. This design uses none — anonymous public-catalogue
  fetching only. If a retailer ever grants API access, that is a **different
  project** with different terms and the risk assessment must be re-run (§9).
  There is therefore no secret to store.
- **The `User-Agent` contact address** is configuration, not a secret, but it is
  real contact information and belongs in config, not hardcoded across files.
- **No PII, ever** (§4.4, cond 7). The adapter must never fetch or store review
  text, staff names, or anything attached to a person. `RawOffer` has no field
  for it; the adapter must not add one.
- **The tripwire stays.** `resolve_source` refusing `LIVE_ACQUISITION=1` is
  removed only in the change that adds the live source, and that change is where
  the §8 checklist is verified. Production-mode fail-closed (tech.md) still
  applies: a production stage without the acquisition controls configured must
  refuse, not silently fetch.

---

## 8. Observability and cost

- **Per-retailer counts** already flow through `refresh()`'s return into the
  Step Functions execution history (`fetched`, `written`, `added`, `changed`,
  `rejected`). Live acquisition adds: requests made, bytes fetched, robots
  re-fetch outcome, and any block/backoff event — so a retailer starting to
  block us is visible immediately, not discovered by a silent empty refresh.
- **A block must alarm.** A retailer returning 403/429 is condition 4's "the
  answer is stop" — it should page an operator, because continuing would be the
  one thing the assessment forbids. This is a new CloudWatch alarm on a new
  metric.
- **Cost is bounded by the daily schedule**, not traffic: one sitemap walk +
  N product fetches per retailer per day. Predictable, and a Budget line already
  exists. The compute is trivial; the risk is reputational and legal, not
  financial, which is why this document is mostly about controls rather than
  cost.

---

## 9. Testing strategy (how it stays offline)

- **Recorded cassettes** (`fixtures/acquisition/<retailer>/`): saved
  robots.txt, sitemap XML, and a sample of product pages, committed. Tests run
  the full adapter against these with `RecordedHttpClient` — no network, CI-safe.
- **The robots gate is tested with an adversarial cassette**: a robots.txt that
  disallows the sitemap, or a product URL under a `Disallow`, must cause the
  adapter to skip/refuse, and a test asserts it does.
- **The "stop on block" path is tested**: a cassette returning 403/429 must
  produce a stop-and-report, and a test asserts the adapter does NOT retry
  harder or construct an alternate URL.
- **A test asserts the search endpoint is never constructed** — grep the
  adapter's emitted URLs in a run and assert none matches the disallowed search
  paths. The strongest form of condition 3: not "we don't call it" but "there is
  no code path that could".
- **`RawOffer` facts-only** is already enforced by the dataclass; a test
  asserts the adapter populates only those fields.

---

## 10. Implementation plan (when the gate clears)

In order, each its own reviewable increment:

1. **Permission + condition 1** (no code): send the email; a human completes the
   §2 table. If refused, stop here — 11.4 does not happen.
2. **`LiveHttpClient` + `RecordedHttpClient`**: the injected HTTP boundary, with
   rate limiting, backoff, hard-stop ceiling, `User-Agent`. Tested against
   cassettes only. No live traffic yet.
3. **`FoodstuffsSource`** (the robots gate + sitemap walk + product fetch +
   normalise stages), Pak'nSave and New World, against recorded cassettes.
4. **Wire into `resolve_source`**: replace the `LIVE_ACQUISITION` refusal with a
   guarded live path that ALSO verifies the §8 checklist state (e.g. a
   `config/acquisition.json` recording which retailers are permitted and
   condition 1's completion). The tripwire becomes a gate, not a hole.
5. **Egress allowlist + the block alarm** (IAM/networking + CloudWatch).
6. **A single, supervised live smoke run** against Foodstuffs, rate-limited, one
   store, recorded — the equivalent of the reviewer's prototype invoke: prove it
   works once, under observation, then decide about the schedule.
7. **Woolworths** only after its terms are confirmed and, ideally, permission
   granted — never if bot mitigation is in the way.

Each step is offline until step 6, exactly like the reviewer workstream: build
and prove the whole thing against recorded data, then take one careful live step
under observation.

---

## 11. What would change this design

- **Permission granted** → conditions 2–6 stay (good manners and Fair Trading
  don't depend on permission), but the risk posture relaxes and Woolworths
  becomes buildable. Re-run the assessment (§9) to capture the new terms.
- **Permission refused** → 11.4 does not happen. The project continues on
  recorded data (lineage B + fixtures), which is where it is today and which is
  a perfectly good place to be for a workshop. This document closes as
  not-pursued.
- **A retailer offers an API** → a different project, different terms, re-run
  the assessment. The sitemap adapter would likely be retired in favour of the
  API, and the credential handling this design deliberately avoids becomes real.
- **Move from workshop to public/commercial** → §5's mitigating regulatory
  posture and §4.1's browsewrap weakness both shift; the whole assessment is
  re-run before a single request.

---

## 12. Reasoning record (dated)

*Written so a future reader can absorb the decisions, not just the result.*

**2026-09-02 — design-doc-only, permission-first, parked deliberately.** The
temptation with acquisition is to build the sitemap parser because it is the
interesting part. The risk assessment is explicit that the parser is not the
gate — permission is — and that the binding legal constraint (Fair Trading,
§4.5) sits on our OUTPUT, which is already controlled, not on our fetching. So
the highest-value artefact is this document plus an email, not code. Building
the adapter now would be spending a fortnight on a source we may never be
allowed to use, and every control it needs is already understood. Parked with a
full design is the honest state: ready to build, deliberately not built.

**2026-09-02 — the adapter is a fetcher and nothing more.** It was tempting to
have the live source also decide staleness or plausibility, since it is closest
to the data. Rejected: `reject_implausible`, `diff_items`, the freshness gate,
and the grounding checks all already exist and are tested, and putting any of
that logic in the adapter would duplicate a control and create the two-sources-
of-truth problem the whole project avoids. The adapter turns a catalogue into
`RawOffer`s; every judgement about those offers happens in code that already
guards the fixture and lineage-B paths identically.

**2026-09-02 — "a block is the answer" is encoded, not just documented.** §4.2
is the one place with criminal exposure, and the bright line is circumventing a
technical control. A design that merely says "don't work around blocks" relies
on nobody later adding a retry-harder branch. So the design makes the failure
path structural: the HTTP client has a hard-stop ceiling and the only branch on
a block is stop-and-report — there is deliberately no code path that rotates a
UA, changes IP, or seeks an alternate endpoint, and a test asserts the search
endpoint cannot even be constructed. The safe behaviour is the only behaviour
the code can express.

**2026-09-02 — Foodstuffs first, Woolworths gated harder.** The assessment
ranks Woolworths NZ highest-risk (unretrievable terms, apparent bot mitigation,
a parent with an explicit prohibition). Building all three at once would treat
them as equivalent when they are not. Foodstuffs (no access prohibition,
published sitemaps) is buildable under the conditions; Woolworths waits for a
human to read its terms and, ideally, permission — and never proceeds if bot
mitigation is in the path.
