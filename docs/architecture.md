# Northlight Support Chatbot: architecture

A design record for the Northlight customer support assistant. It explains what
the system does, how a single request flows through it, and why each significant
decision was taken rather than an obvious alternative.

Written to be read alongside the code. Section headings map onto the report
chapters they are intended to support.

---

## 1. Problem and scope

Northlight is a fictional Vietnamese consumer electronics retailer. Its support
desk answers the same three kinds of question over and over:

1. **Product consultation.** "I need a power bank under 500,000đ." The shopper
   describes a need rather than naming an item.
2. **Policy questions.** "How long do I have to return this?" The answer is a
   business fact that lives in a written policy and changes over time.
3. **Order tracking.** "Where is my order?" The answer is private data belonging
   to one specific customer.

These three look similar from the chat window and are completely different
underneath. The first is a ranking problem. The second is a retrieval problem
where being wrong is worse than being unhelpful. The third is an authorisation
problem wearing a conversational disguise.

### The multi-channel setting

Northlight sells through four channels: its own website, and the Shopee, Lazada,
and TikTok Shop marketplaces. This is the ordinary shape of a Vietnamese retailer
of this size, and it is the reason the support problem is worth automating.

Each marketplace provides its own seller chat. None of them can see the other
three. A seller working this way answers the same return-policy question in four
separate inboxes, and a shopper's answer depends on which app they happened to
open. Order data is fragmented the same way: each platform issues its own order
code in its own format, and the seller's internal code is never shown to the
shopper at all.

This system is the company's own consolidated support site. It is not a bot
deployed into a marketplace's chat window. One assistant, one policy corpus, and
one order store cover every channel. The shopper reaches it from the company's website or from a link
in a post-purchase message, and it answers about an order no matter where that
order was placed.

Two consequences run through the rest of this document:

- **An order has two identities.** Its internal `order_code`, and the
  `channel_order_code` the marketplace issued. Lookup accepts either, because the
  shopper only ever saw the second one. See §7.
- **Policy answers are channel-dependent.** Return windows, refund destinations,
  and shipping fees genuinely differ per marketplace, so the corpus carries
  per-channel sections and the assistant must follow the order's channel rather
  than quoting the website's terms. See §5.

### Why no marketplace API integration

Integrating Shopee's or Lazada's Open Platform API would require an approved
partner account and per-shop OAuth, neither of which is available for a student
project, and neither of which this design needs.

It is also not how the problem is solved in practice. A seller at this scale
already runs an order management system (Sapo, KiotViet, Haravan, or a
spreadsheet) into which marketplace orders are exported and normalised. The
order store in this system stands in for exactly that: records the seller already
possesses, carrying the channel they came from. Adding a live API would change
where the rows come from, not how the assistant reasons about them.

**Where the data would come from in production.** The seller owns every order,
wherever it was placed, and can read it:

| Order | Status and tracking source |
|---|---|
| Website | The seller's own system; the seller books the carrier |
| Marketplace, seller-arranged shipping | The seller's own carrier booking, as above |
| Marketplace, platform-arranged shipping (SPX, Lazada Express, ...) | The marketplace's seller API, which exposes the seller's orders and their logistics events; the same data Seller Centre shows |

What the seller does not get for platform-shipped orders is a direct line to
that courier or anything the platform chooses not to share. Carrier events name
places ("arrived at the Đà Nẵng sorting hub"), never live coordinates, in every
case. The tracking map (§4, *Routes*) is built to that level of detail and no
finer, so it shows only what a real seller could actually know.

The assistant serves **Vietnamese and English**. All source code, identifiers,
and documentation are English; the two languages appear only as content.

### Explicit non-goals

- The assistant never places, pays for, or modifies an order. It reads; it never
  writes to commerce state. This keeps the blast radius of a wrong answer to
  misinformation rather than money. Orders are placed by the storefront's guest
  checkout (§2, *Storefront*), which takes no real payment.
- No general conversation. Anything outside the three jobs is declined.
- No claims about products the store does not stock.
- **No live marketplace integration.** No Shopee, Lazada, or TikTok Shop API is
  called, and the assistant is not deployed into those platforms' chat windows.
  It answers about marketplace orders from the seller's own consolidated records.
- **No claims about a marketplace's own business.** Its prices, its promotions,
  its other sellers, or the state of a shopper's account there. An evaluation
  case asserts this refusal.

---

## 2. System overview

```
                     Angular 22 SPA
                Azure Static Web Apps
                          │
                  HTTPS · Server-Sent Events
                          │
                          ▼
                       FastAPI
               Azure Container Apps (scale to zero)
                          │
     ┌────────────────────┼────────────────────┐
     │                    │                    │
 Classifier          Orchestrator          Telemetry
 Haiku 4.5           Opus 5                one row per turn
 intent, language    hand-written
 safety              tool loop
                          │
                    ┌─────┴─────┐
                    │ Six tools │
                    └─────┬─────┘
                          ▼
                  MongoDB Atlas M0
    products · policies · orders · conversations · events · handoffs
              Automated Embedding via Voyage AI
```

Both model calls, the classifier's and the orchestrator's, reach Claude through
one of two providers: Anthropic's API directly, or Snowflake Cortex. They speak
the same Messages API, so nothing in the diagram changes between them. The choice
and its limits are in §9, *Model provider*.

### Storefront

The website also sells: a catalogue, product pages, a cart kept in the browser,
and a guest checkout (`app/api/shop.py`, `frontend/src/app/shop/`). It exists to
make the business visible. Each product records the channels it is listed on,
and the product page shows them, so the multi-channel premise of §1 is on screen
rather than asserted. It is deliberately small: no accounts, and a demo payment
step that takes no money.

Two properties matter. Checkout prices every line from the database and ignores
anything price-like from the client, and it reserves stock with conditional
decrements, rolling the whole cart back if one line fails. And an order placed
here is an ordinary website order, stored with hashed contact details and a
planned route, so the assistant tracks it like any other. That gives the demo a
closed loop: buy on the site, then ask the assistant where the order is.

### Component responsibilities

| Component | Responsibility |
|---|---|
| Angular SPA | Renders the conversation, product cards, and citations; streams the reply |
| FastAPI | HTTP surface, streaming transport, persistence, aggregation |
| Classifier | Labels language, intent, and safety before the agent runs |
| Orchestrator | Runs the tool loop, emits progress, applies output checks |
| Tools | The only route to data; each returns citable identifiers |
| MongoDB Atlas | Single datastore for documents, vectors, and telemetry |

---

## 3. The life of one turn

This is the core of the system and the section worth reading closely.

```
shopper message
      │
      ▼
[1] classify            Haiku 4.5, structured output
      │                 → language, intent, safety, confidence
      ▼
[2] screen              short-circuit?  ──yes──▶ canned reply ──▶ [7]
      │ no
      ▼
[3] build request       cached system prompt + tool defs + replayed history
      │
      ▼
[4] stream from model ◀──────────────────┐
      │                                  │
      ├─ text deltas ──▶ to the browser  │
      │                                  │
      └─ stop_reason == tool_use?        │
                │ yes                    │
                ▼                        │
        [5] run tools concurrently       │
            all results in ONE message ──┘
                │ no (end_turn)
                ▼
[6] output checks       citations validated, figures checked
      │
      ▼
[7] done                persist conversation + telemetry
```

### Step 1: classify

A small, cheap model labels the turn before the expensive one sees it. It
returns language, intent, a safety label, and a confidence score, using
structured output so the result is schema-valid by construction.

Three reasons this exists rather than letting the main agent handle everything:

- **Language.** Tools need to know which language to return content in, and that
  decision has to be made before any tool runs.
- **Measurability.** Intent routing accuracy is a number the evaluation harness
  reports. Without an explicit label there is nothing to measure against.
- **Cost.** A confidently out-of-scope turn is answered without paying for a full
  agent turn.

**It is advisory, never authoritative.** If the classifier call fails for any
reason it returns a default carrying `confidence = 0.0`, and the turn proceeds.
A classifier outage degrades quality; it must not take the assistant offline.

### Step 2: screen

Only two cases short-circuit:

- **Payment credentials.** A shopper pasting an OTP or a card number gets a fixed
  warning. An improvised reply here is a genuine risk and there is no upside to
  letting the model compose one.
- **Confidently out of scope.** Requires `confidence >= 0.85`, which a failed
  classifier can never reach. Without that threshold, a classifier outage would
  turn the assistant into something that declines every real question.

Prompt injection attempts are deliberately **not** short-circuited. The system
prompt handles them better than a keyword rule, and a rule broad enough to catch
injection also catches shoppers who merely ask how the assistant works.

### Steps 4 and 5: the tool loop

Bounded at **6 rounds**. Without a bound, a confused model can loop until it
exhausts the context window, which is slow and expensive and produces nothing.

Two properties of the loop matter:

**Parallel tool calls return in a single message.** When the model requests
several tools at once, they execute concurrently and every result goes back in
one user message. Splitting results across messages teaches the model to stop
requesting tools in parallel, which permanently degrades latency.

**Tool failures are reported, not hidden.** A failing tool returns a
`tool_result` marked as an error rather than being dropped. The model needs to
know a lookup failed so it can say so, rather than inferring silence means
nothing was found.

### Step 6: output checks

Two checks run over the finished answer, with deliberately different severity.

**Citation validation is enforcing.** Every `[ref:...]` marker is checked against
the identifiers tools actually returned this turn. A marker naming anything else
is stripped from the text. A fabricated citation is a defect with no legitimate
cause, and leaving it in would render as evidence for a claim that has none.

**Numeric grounding is advisory.** Figures in the answer are compared against
figures in the tool results. A mismatch sets a flag recorded in telemetry and
surfaced in the review queue, but does not block the reply. The check is a
heuristic with real false positives, and silently withholding a probably-correct
answer is worse than flagging it for review.

The distinction is the point: enforce what has no false positives, measure what
does.

---

## 4. Data model

Six MongoDB collections. Bilingual fields sit side by side rather than in a
nested language map, because Atlas Search filters and sorts read them directly.

| Collection | Contents | Notes |
|---|---|---|
| `products` | 480 items across 8 categories | `embedding_source` combines both languages |
| `policies` | 26 chunks from 5 documents | `chunk_id` is the citation anchor |
| `orders` | 325 orders across 4 sales channels | Contacts stored only as salted hashes; two lookup codes |
| `conversations` | One document per session | Messages appended, not rewritten |
| `events` | One row per user turn | Feeds the dashboard |
| `handoffs` | Human agent queue | |

### Channel on the order

`channel` is one of `website`, `shopee`, `lazada`, `tiktok_shop`. It decides
which policy passage answers a return or refund question, and it is the field the
dashboard would break operating figures down by. Marketplace
orders additionally carry `channel_order_code`, indexed unique with a **partial
filter** on the string type. Website orders store the field as an explicit null,
and a unique index over those nulls rejects every website order after the first.
The first version used a *sparse* index, which looks like the right tool and is
not: sparse skips only documents where the field is absent, not null. No unit
test could catch that; the first seed against a real cluster did, and a
regression test now pins the partial filter.

### Routes

Each order carries a destination and a route: warehouse, regional sorting hubs,
destination, with the time the parcel reached each stop. Orders store place keys;
coordinates live in one table (`app/geo.py`). That mirrors how carrier data
arrives, as named scan points, and lets the map be corrected without touching
order data.

The destination is a **province, never an address**. The map needs nothing finer,
and anything shown in the chat window is visible to whoever holds the order code
and contact. `get_order_status` returns place names to the model, so it can answer
"where is it now", and a separate payload with coordinates to the interface. That
payload exists only after the contact check passes.

The map is committed SVG geometry generated from Natural Earth
(`frontend/scripts/build_vietnam_map.py`), not a tile service. No third party
receives the shopper's route, the demo works offline, and the map shows Hoàng Sa
and Trường Sa, which Natural Earth does not assign to Vietnam and common tile
sets omit or label otherwise.

### One embedded field per document

Every searchable document carries an `embedding_source` field combining
everything worth searching on, in **both languages**. That single field is what
gets embedded.

The consequence is that a Vietnamese question and an English question land near
the same vector, so `"noise cancelling headphones"` and `"tai nghe chống ồn"`
retrieve the same products without maintaining two indexes or translating
queries at runtime.

---

## 5. Retrieval design

### Hybrid search

Product search runs two retrievers and fuses the results.

| Retriever | Strength | Weakness |
|---|---|---|
| Vector (`$vectorSearch`) | Meaning: "ấm cho mùa đông" finds heaters | Exact tokens: model numbers, brand names |
| Keyword (`$search`) | Literal matches | Paraphrase, cross-language |

Neither is sufficient alone. A shopper searching for a specific model number
wants a literal match; a shopper describing a need wants semantic similarity.

**Fusion uses Reciprocal Rank Fusion** with `k = 60` and weights `[1.0, 0.6]`
favouring the semantic list. RRF scores a document by `1 / (k + rank)` summed
across the lists it appears in.

RRF rather than score normalisation because a vector similarity and a Lucene
relevance score are not on comparable scales, and any normalisation between them
would be arbitrary. RRF uses only rank, so the question never arises.

Fusion runs in Python rather than through MongoDB's `$rankFusion` so the
behaviour is identical on any cluster tier and is unit tested without a database.

**Both retrievers over-fetch** before fusion, taking `min(max(limit * 4, 20), 50)`
candidates each. Fusing two lists of exactly `limit` items would mostly reproduce
the vector ranking and waste the keyword half.

### Filters run before the vector comparison

Category, price, rating, and stock are declared as filter fields in the index, so
`$vectorSearch` narrows candidates *before* comparing vectors. This is what makes
"jackets under 500,000đ rated above 4 stars" both correct and fast.

One subtlety: the index filters on **list price**, because that is the indexed
field, and discounted items are re-checked after retrieval. A product whose sale
price falls under the ceiling is therefore correctly included even though its
list price does not. Any assertion about the price filter has to test the
effective price, not the list price.

### Policy chunking

**One chunk per hand-authored section**, with an explicit slug in the source
markdown, producing citation anchors like `return-policy#refund-timing`.

A token-count chunker was rejected. It would renumber every chunk whenever a
policy was edited, and every citation in the evaluation dataset would rot. Chunk
stability is what makes citations meaningful over time, and hand-authored
sections also align chunk boundaries with topic boundaries, which a fixed window
does not.

26 chunks from 5 documents, each 300 to 900 characters per language.

This stability paid off when the per-channel sections were added. Three new
sections (`return-policy#marketplace-returns`,
`return-policy#marketplace-refunds`, and `shipping-policy#marketplace-shipping`)
appended three new anchors without renumbering or invalidating a single existing
citation. A token-count chunker would have rotted the whole evaluation set.

**Per-channel policy is split by question rather than bundled per channel.**
Returns and refunds for marketplace orders occupy two sections, because a shopper
asks about them separately. The first draft bundled them into a single
1,242-character chunk, roughly two and a half times the corpus average. A chunk
that long dilutes its own embedding and retrieves worse for both questions than
either half does alone.

### Embedding is pluggable

`EMBEDDING_MODE=auto` uses Atlas Automated Embedding: Atlas calls Voyage AI on
insert, on update, and at query time, and this codebase never computes a vector.
`explicit` computes them here instead.

The switch exists because Automated Embedding is a public preview feature. All
mode-specific logic is confined to one module; query code is identical either
way. This is risk management, not indecision: a preview API is a reasonable
dependency only when abandoning it is cheap.

---

## 6. Why the agent loop is hand-written

The Anthropic SDK ships a tool runner that drives this loop. It was not used.

| Requirement | Tool runner |
|---|---|
| Persist conversations | Keeps its own history and does not expose it, so history must be mirrored anyway |
| Show which tool is running | No hook to emit progress mid-turn |
| Guardrail pass between rounds | Not expressible |
| Stability for a demonstration | It is a beta surface |

The first point is decisive. Once history has to be mirrored regardless, the
runner's main benefit is gone, and what remains is a beta dependency that cannot
emit the events the interface needs.

A secondary reason matters for this project specifically: a hand-written loop can
be explained and defended. A delegated one can only be described.

---

## 7. Security: order lookups

The threat model worth writing up, because it is the one place where a wrong
design leaks real data.

### The attack

Order codes look like `DH2026090001`. They are short, sequential, and routinely
shared in screenshots and group chats. If an order code alone unlocked an order,
anyone could enumerate codes and read other customers' names, addresses, and
purchase history.

### The design

`get_order_status` requires the order code **and** a matching contact detail.
Four properties, each closing a specific hole:

**1. Contacts are stored only as salted hashes.** The database holds no readable
phone number or email, and neither does anything the model sees. Verification
hashes the supplied value and compares digests.

The salt is not password hygiene theatre. A Vietnamese mobile number has around
ten digits of entropy, so an unsalted hash falls to a rainbow table immediately.

**2. Comparison is constant-time.** `hmac.compare_digest`, so the duration of a
failed check does not reveal how much of the digest matched.

**3. Fragments are rejected.** Supplying the last four digits is refused. Four
digits is 10,000 guesses, which is not a credential. The system prompt instructs
the assistant to ask for the full number rather than accept a fragment.

**4. Failures are indistinguishable.** A wrong contact and a non-existent order
code return the **byte-identical** response. Distinguishing them would turn the
tool into an oracle for which order codes exist, which is exactly the
enumeration the design is meant to prevent.

The system prompt reinforces this: never confirm or deny that an order code
exists, and never echo a full phone number. The tool returns a masked form
(`******4567`) for the shopper to recognise.

### Two codes, one credential

A marketplace order carries two identifiers: the seller's internal `order_code`
and the `channel_order_code` the platform issued. The shopper has only ever seen
the second one. Requiring the first would make order tracking unusable for the
majority of orders, so lookup matches on either:

```python
{"$or": [{"order_code": supplied}, {"channel_order_code": supplied}]}
```

Adding a second lookup key to an authorisation path deserves justification.
Neither code is a credential; both are identifiers. The contact detail is the
credential, and it is checked identically no matter which key found the row. The
wider key space therefore changes which orders can be addressed, without changing
which orders can be read.

The two spaces are also kept disjoint, so that a marketplace code can never
collide with an internal code and a single supplied string resolves to at most
one order. Tests assert the disjointness and the uniqueness of each space,
`seed.smoke` asserts that a marketplace code with a wrong contact returns the
same refusal as an internal code with a wrong contact, and evaluation case
`ord-sec-12` asserts the same property end to end through the model.

### Evaluation coverage

Five dataset cases probe this directly: a wrong phone number, a four-digit
fragment, an asserted claim of ownership, prompt injection asking the assistant
to bypass verification, and a marketplace order code supplied with a contact that
does not match it. Each asserts that no order status appears in
the reply, with matching that folds Vietnamese diacritics so `"dang giao"` is
caught as well as `"đang giao"`.

---

## 8. Grounding

The central rule: **every factual claim must come from a tool result in the
current turn.**

Prices, stock, specifications, return windows, fees, warranty terms, delivery
estimates, and order status may never be stated from model memory. These are
business facts that change, and a plausible wrong number is worse than an honest
"I cannot confirm that", because the shopper cannot tell the difference.

Three mechanisms enforce it, in increasing order of reliability:

1. **Prompt instruction.** Necessary but not sufficient on its own.
2. **Tool design.** Policy search returning nothing responds with explicit
   guidance to decline and offer a human, rather than an empty list the model
   might paper over.
3. **Post-answer validation.** Citation markers are checked against real sources
   and stripped if fabricated; figures are checked against tool results and
   flagged.

Retrieved content is treated as **data, never instruction**. Product
descriptions, policy text, and order notes come from a database. The system
prompt states that any instruction appearing inside them is ordinary text.

---

## 9. Cost control

### Prompt caching

The system prompt (6,033 characters) and the six tool definitions do not change
between turns, so a cache breakpoint sits at the end of the system block. Tools
render before the system prompt in the cached prefix, so one breakpoint covers
everything stable.

**Nothing volatile may enter that prefix.** A timestamp or a session identifier
in the system prompt invalidates the cache for every conversation at once, and
the only symptom is a bill several times larger. The dashboard reports cache hit
rate specifically so this failure is visible.

### Model tiering

Which model does what depends on the provider (§9, *Model provider*):

| Task | Anthropic / Cortex | Azure OpenAI (in use) | Why |
|---|---|---|---|
| Turn classification | `claude-haiku-4-5` | `gpt-5-mini`, minimal reasoning | Bounded labelling, runs on every turn |
| Agent | `claude-opus-5` | `gpt-5-mini`, medium effort | Tool selection and grounded synthesis |
| Rubric judging | `claude-opus-5`, low effort | `gpt-5-mini`, low effort | A weak judge mistakes fluent wrong answers for correct ones |

On Azure one model fills every role because the student subscription has quota
for no stronger one (§9, *Model provider*). Tiering survives as effort levels
rather than as separate models.

### Model provider

The model is reached through one of three providers, chosen by `LLM_PROVIDER`:

| Provider | Wire format | Billed as | Status |
|---|---|---|---|
| `anthropic` | Anthropic Messages API | Anthropic API usage | Supported; the code default |
| `cortex` | Anthropic Messages API, via Snowflake Cortex | Snowflake credits | Supported; **unavailable on trial accounts** |
| `azure_openai` | OpenAI Chat Completions, via Azure OpenAI v1 | Azure usage | **In use:** `gpt-5-mini` |

**The deciding constraint was billing, not capability.** The project runs on an
Azure for Students subscription with a $100 credit and no card, and the choice
went through three stages, each ended by a verified fact rather than a
preference:

1. *Claude on Azure (Microsoft Foundry).* Microsoft's deployment guide excludes
   subscriptions without a pay-as-you-go billing method, naming student and
   credit-only accounts, so the student credit cannot pay for Claude at all.
2. *Claude through Snowflake Cortex*, paid from Snowflake trial credits. The
   integration was built and the credentials provisioned, and every Cortex call
   then returned `403 / 003001, "This account is not allowed to access this
   endpoint"`. Running the SQL equivalent in the same account gave the cause
   plainly: *"AI function COMPLETE is not available for trial accounts."*
   Snowflake's REST API documentation does not mention this.
3. *Azure's own models (Azure OpenAI)*, which are billed as ordinary Azure usage.
   Before any code was written, the subscription's quota was listed region by
   region: across all GPT models, only `gpt-5-mini` (500K tokens a minute) and
   `o4-mini` have any GlobalStandard quota, in Japan East and Korea Central.
   `gpt-5-mini` is deployed in Japan East at 100K tokens and 100 requests a
   minute, priced at $0.25 input and $2.00 output per million tokens.

The cost of that path is model strength. `gpt-5-mini` is a smaller model than
Claude Opus 5, and the system prompt and tool descriptions were written for
Claude. How much that matters is a question for the evaluation (§10), not for
assumption.

**One neutral interface, two wire formats.** The agent loop, classifier and
judge call `app/agent/llm.py`, which offers four operations: stream a round,
report the tool calls requested, accept tool results, and return
schema-validated structured output. Each backend keeps its provider's native
message list between rounds, so nothing is translated back and forth. The
formats differ in exactly the places that are easy to get wrong:

| Concern | Anthropic | OpenAI Chat Completions |
|---|---|---|
| Tool calls | Complete `tool_use` blocks | JSON argument fragments, reassembled by index |
| Tool results | `tool_result` blocks in one user message | One `tool` message per call, after the assistant message that made the calls |
| Failed tool | `is_error: true` | No flag; the payload carries an `error` key |
| Structured output | `output_config` format | `response_format` JSON schema |
| Reasoning depth | adaptive thinking + `effort` | `reasoning_effort` |
| Prompt caching | explicit `cache_control` breakpoint | automatic on long prefixes |
| Refusal | `stop_reason: refusal` | `refusal` text, or the content filter stopping |

Two details deserve mention. OpenAI's strict tool mode demands that every
property be required; the product search's filters are genuinely optional, so
strict is sent only for schemas that qualify, and a mismatched call still
surfaces as an error the model can read. And arguments cut off by the output
limit arrive as incomplete JSON; the call is marked malformed and reported back
to the model rather than run on a guess.

**The probe.** `python -m app.agent.probe` exercises each capability through the
same interface the app uses: a streamed text round, a full tool round trip
(call, result, answer), and structured output with the real classifier and judge
schemas. It also tests the two features that differ between providers, `strict`
tool schemas and effort, and reports the value for each switch
(`LLM_STRICT_TOOLS`, `LLM_EFFORT`). It is to the provider what `seed.smoke` is to
retrieval. On the deployed `gpt-5-mini` every check passes.

**Credentials** are declared `SecretStr` settings, which keeps them out of reprs,
logs and tracebacks. Declaring them also fixed an older fault: pydantic-settings
loads `.env` into declared fields only, never into the process environment where
the Anthropic SDK looked, so a key in `.env` had never reached it during local
runs. Azure hid the bug by injecting real environment variables.

**Cost estimates** in telemetry use per-provider prices set in `.env`, so they
track the real Azure bill. Under Cortex they would be the Anthropic list-price
equivalent, not the Snowflake credits billed.

### History replay

The last **12 turns** are replayed as plain text. Tool calls and their results
are not replayed: they would multiply context size, and stale retrieved content
is worse than none, because prices and order statuses change between turns. The
model can call a tool again if it needs current data.

---

## 10. Evaluation methodology

56 labelled bilingual cases across four suites.

| Suite | Cases | Measures |
|---|---|---|
| Policy | 20 | Retrieval and factual accuracy, including 4 per-channel cases |
| Product | 10 | Filter honouring and recommendation quality |
| Order | 12 | Verification behaviour, including 5 security cases and 2 marketplace-code lookups |
| Guardrail | 14 | Refusals, injection resistance, escalation |

Seven cases cover the multi-channel behaviour specifically: resolving a Shopee
and a TikTok Shop order code, refusing a marketplace code paired with a
mismatched contact, and four policy questions whose correct answer depends on
the channel the order came from. Three of those four are written so that quoting
the website's terms scores as wrong rather than merely incomplete, because that is
the failure mode this design is exposed to.

### Metrics

| Axis | Method |
|---|---|
| Intent routing | Classifier label against a hand-labelled expectation |
| Retrieval | recall@3, recall@5, MRR against gold chunk ids |
| Tool selection | Whether any expected tool was called |
| Answer quality | Rubric graded 1 to 5 by a judge model |
| Groundedness | Whether every figure appears in a tool result |
| Security | Whether an order was disclosed without matching contact |
| Cost and latency | Recorded per turn from API usage |

### Design decisions

**Metrics are pure functions.** All scoring lives in a module with no network
calls, so every metric is unit tested without spending money. The tests are part
of the backend's test suite (363 tests).

**Gold labels are validated against the corpus.** A test asserts every
`gold_chunks` entry names a chunk that actually exists. A typo there reports
retrieval as broken when retrieval is fine, which is an expensive thing to debug
after paying for a full run.

**Retrieval scores over policy sources only.** Product and order identifiers also
appear in the retrieved list, and counting them would depress recall for reasons
unrelated to retrieval quality.

**Tool selection accepts any expected tool.** Comparing two products via
`compare_products` or via two `get_product_details` calls are both correct.
Scoring one as a failure would measure conformity rather than capability.

**Each case runs in a fresh session**, so no case sees another's history and
dataset order cannot affect results.

**The runner requires confirmation before spending.** It prints a cost estimate
and waits. Concurrency is bounded by a semaphore, because firing 56 turns at once
produces rate limit errors rather than results.

### Results

Run history, the judge calibration and per-case analysis are in
[`evaluation.md`](evaluation.md).

---

## 11. Deployment

| Layer | Service | Rationale |
|---|---|---|
| Frontend | Azure Static Web Apps (Free) | Static bundle on a CDN |
| Backend | Azure Container Apps | Scales to zero, streams without buffering |
| Database | MongoDB Atlas M0 (Free) | Documents, vectors, and telemetry in one place |
| Model | Azure OpenAI `gpt-5-mini`, Japan East | See §9, *Model provider* |

Container Apps rather than App Service or Functions for three specific reasons:
it scales to zero so an idle demonstration costs nothing; it streams
Server-Sent Events without buffering; and it takes a container image directly, so
what runs in Azure is what was built.

The image is built with `az acr build`, in Azure rather than locally. Building on
an Apple Silicon machine and pushing produces an arm64 image that fails with
`exec format error`.

The browser calls the backend directly. Static Web Apps cannot rewrite `/api` to
an external host (rewrite targets must be paths inside the app), and linking a
Container App as its managed API needs the paid Standard plan. So the bundle reads
the backend's address at runtime from `config.js`, which is empty in development,
where the Angular dev server proxies `/api`, and written by
`infra/deploy-frontend.sh` in production. One build serves both. The backend
admits the site's origin through `CORS_ORIGINS`.

Costs on the student credit: Container Apps on the consumption plan scale to zero
and fall inside the monthly free grant at this traffic; the Basic container
registry is the one fixed cost, about 5 USD a month; Static Web Apps Free and
Atlas M0 cost nothing.

Secrets are Container Apps secrets referenced by `secretref:`, never baked into
the image. The deploy script reads `LLM_PROVIDER` and sends only the credential
that provider needs.

For Cortex, `infra/snowflake/setup.sql` creates the credential once. The backend
never signs in as a person: it authenticates as `CHATBOT_SVC`, a service user with
no password, whose token is restricted to a role granting nothing but the right to
call Cortex models. A leaked token can spend credits; it cannot read or change
data. Two defaults in Snowflake would otherwise have broken the demonstration.
Tokens expire after 15 days, shorter than the 30-day trial, so the script sets 45.
And a token only works for a user under a network policy, while neither Container
Apps nor a developer laptop has a fixed outbound address, so the policy admits
any IPv4 address, the same trade-off made for Atlas network access.

---

## 12. Known limitations

Stated plainly, because a defence goes better when the author raises these first.

**The admin dashboard has no authentication.** It exposes operating cost and
quotes shopper messages. It needs a gate before any public exposure.

**All data is synthetic.** The catalogue, orders, and policy documents were
generated or written for this project.

**No live marketplace integration.** Marketplace orders are seeded records
carrying a channel and a platform-shaped order code, standing in for what a
seller would export from Shopee, Lazada, or TikTok Shop into their order system.
This is a deliberate scope decision, argued in §1, not an unfinished feature: the
partner API access it would require is not available to this project, and it
would change where the order rows come from rather than how the assistant
reasons about them. The ingestion path is what remains untested as a result:
reconciling exports, handling a platform's status vocabulary, and dealing with
codes that change after a split shipment.

**The model is smaller than the one the prompts were written for.** The system
prompt and tool descriptions were developed against Claude, and the deployed
model is `gpt-5-mini`, chosen because it is the strongest model the student
subscription has quota for. The evaluation measures the result; a comparison
against Claude on the same dataset would isolate the model's share of any gap.

**The subscription region list constrains placement.** An Azure for Students
subscription may deploy only to `koreacentral`, `japanwest`,
`indonesiacentral`, `japaneast` and `eastasia`. The backend runs in East Asia
(Hong Kong) and the model in Japan East, because East Asia offers only
provisioned model capacity.

**Automated Embedding is a public preview feature,** and on a free M0 cluster
its query rate depends on billing: 3 queries a minute without a payment method
on the Atlas organisation, 2,000 with one. The first real run hit the lower
limit; a payment method was added, and the free token allowance means nothing is
charged at this volume. The `explicit` fallback exists for these reasons but has
not been exercised end to end.

**Verification assumes the seller holds the buyer's contact.** Marketplaces
increasingly mask the buyer's phone number and address from sellers. For such
orders, "order code plus phone" could not verify, and a real deployment would
need another second factor, such as the marketplace username.

**Marketplace rules.** Platforms discourage moving buyers off-platform. A real
seller would present this site as after-sales support for its own customers and
check each marketplace's policy.

**The storefront is a demonstration.** No accounts, no real payment, and
delivery to province level only. Stock is reserved at checkout, but nothing
restocks cancelled orders.

**The judge cannot see the data.** It grades a reply against a rubric without the
tool results, so it can mark down a correct specification it has no way to check
(case `prd-en-04`). Grounding is measured separately and deterministically for
exactly this reason.

**Numeric grounding is a heuristic.** It compares figures textually and will
produce false positives, for example when the model correctly rounds or restates
a number in another form. It is reported, never enforced, for that reason.

**Conversation memory is a fixed window.** Twelve turns, with no summarisation.
A long conversation loses its earliest context rather than compacting it.

**Single-turn evaluation.** Every case is one message with empty history. Failures
that only appear across several turns, such as losing track of a product under
discussion, are not measured.

**No load testing.** Latency figures come from evaluation runs against an idle
free-tier cluster and would not survive concurrent traffic.

---

## 13. Possible extensions

Ordered by value relative to effort:

1. **Authentication on the admin router.** Small, and closes a real hole.
2. **Multi-turn evaluation cases.** Would cover reference resolution, the most
   likely untested failure mode.
3. **Conversation summarisation** past the twelve-turn window.
4. **A reranker** over fused results, the usual next gain in retrieval quality.
5. **Streaming thinking summaries** to the interface, which would make the
   assistant's reasoning visible during a demonstration.
6. **An operational and analytical split**, streaming telemetry to a warehouse
   rather than aggregating over the operational store. Only worth doing at a data
   volume this system will not reach, but it is the honest scale-out path.
