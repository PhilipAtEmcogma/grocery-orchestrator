# Open review — what should a one-word grocery query return?

**Status:** open, and wants a person who shops at Auckland Pak'nSAVE or New World.
**Raised:** 2026-08-30 · **Effort:** about fifteen minutes · **You need no code.**

Companion to [`OPEN-REVIEW-min-grams-per-person-day.md`](OPEN-REVIEW-min-grams-per-person-day.md),
and deliberately the same shape: a judgement the build had to make, written up so
somebody better placed can overrule it.

---

## The question, in one line

When a shopper types **"cheapest butter"**, and the catalogue holds fourteen
different butters, **which one should we price?**

## Why we cannot just search

The assistant never guesses at a product. Matching is exact, and the reason is a
real defect from earlier in this project: a substring match once resolved
**"truffle oil"** to **canola oil** and reported its price. The shopper had no
way to tell. Since then the rule has been that no confident match means *no
answer* — under-matching is recoverable, a confidently wrong price is not.

That rule needs a vocabulary: a list saying which typed words mean which
product. Product names generate themselves ("brown onions" → Brown Onions). The
**bare nouns** do not, and that is what needs a human.

## Why picking automatically does not work

The obvious rule — *"take the cheapest thing whose name contains the word"* —
produces this:

| The shopper types | The cheapest name-match is | |
|---|---|---|
| butter | **Salted Butter Frozen Dessert** | ❌ a dessert |
| cheese | **Chunky Cheese Sausages** | ❌ sausages |
| bread | **Banana Bread** | ❌ a cake, really |

So a person picked, once, per word. Those picks are below.

---

## What we chose, and the three we are least sure about

The rule used: **the plainest staple** — the thing you would accept without
comment if a flatmate came back with it.

### The three close calls

**1. "chicken" → a whole chicken (1.35kg)**

The catalogue has 42 chicken products: whole birds, breasts, thighs, drumsticks,
nibbles, wings. We chose the whole bird as the plainest.

*The doubt:* someone budgeting for a weeknight dinner may mean thighs or mince,
not a roasting bird. A whole chicken is also the biggest single purchase in the
list, so it shapes a meal plan more than the others.

> **Better answer?** whole bird · breast · thigh · drumsticks · leave as is

**2. "oil" → 2 litre canola oil**

Also available: 3L canola, 500ml canola, 1L olive oil.

*The doubt:* 2L is a bulk buy. Somebody asking "how much is oil" may be pricing a
normal bottle, and the 500ml is nearer that. But cooking oil is genuinely a bulk
staple for a flat.

> **Better answer?** 2L canola · 500ml canola · 1L olive · leave as is

**3. "sausages" → classic beef sausages (6 × 75g)**

Also available: precooked 1kg, Chinese honey 1kg, "chunky cheese" 1kg, pork.

*The doubt:* pork sausages are arguably the New Zealand default, and the fixture
catalogue used pork. We chose beef because the pack looked like the standard
supermarket six-pack.

> **Better answer?** beef 6-pack · pork · precooked 1kg · leave as is

### The rest, for a quick scan

Change any that look wrong.

| Typed word | Returns | | Typed word | Returns |
|---|---|---|---|---|
| butter | Salted Butter 500g | | bananas | Bananas (per kg) |
| milk | Standard Milk 2L | | apples | Royal Gala (per kg) |
| bread | White Toast Bread 700g | | onions | Brown Onions (per kg) |
| eggs | Size 7 Eggs, dozen | | potatoes | White Washed Potatoes (per kg) |
| yoghurt | Greek Style Natural 750g | | carrots | Carrots (per kg) |
| mince | Beef Mince 1kg | | broccoli | Broccoli (each) |
| rice | Basmati Rice 1kg | | tomatoes | Tomatoes 700g |
| pasta | Penne Pasta 500g | | flour | Plain Flour 1.5kg |
| oats | Rolled Oats 800g | | | |

### Four words we deliberately answer nothing to

The catalogue has no plain staple for these, so rather than point them at
something odd, the assistant says it has no price for them.

| Typed word | Why nothing | Would you rather it returned…? |
|---|---|---|
| **cheese** | 22 matches, no plain cheddar block — only parmesan, mozzarella, slices, grated | mozzarella? grated cheddar? keep saying nothing? |
| **tuna** | Only premium loins and seared saku. No canned tuna. | a loin? keep saying nothing? |
| **salmon** | Only smoked and portioned. No plain fillet. | keep saying nothing? |
| **peas** | The catalogue genuinely has none. | — |

"Cheese" is the one worth arguing about: it is a common word to type, and
answering nothing may read as broken rather than careful.

---

## What is at stake, honestly

**Less than the other open review.** A wrong entry here costs a shopper one
unhelpful answer — they see a price for a butter they did not mean, and can ask
again more specifically. It cannot produce a *wrong price*, because whatever we
return is really that product's real price, and every figure is still checked
against the record it came from.

By contrast `min_grams_per_person_day` decides whether a request is **refused
outright**, which is why that review matters more.

The reason to do this one anyway is that these words are what a demo audience
will type first.

## How to change an answer

Say which word and what it should return — a sentence is plenty. No code
changes: the answers live in `config/product-synonyms.json` under
`catalogues.lineage_b.head_terms`, one line each, and a test checks that
whatever is chosen is at least the right *kind* of thing (a butter must be
dairy, not a frozen dessert).

## What would settle it properly

Real query logs. Once the pilot runs, the words shoppers actually type — and
which answers make them immediately ask again — decide this better than anyone's
judgement. Until then it is a reasoned guess, and this document is the record
that it was one.
