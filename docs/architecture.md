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

The assistant serves **Vietnamese and English**. All source code, identifiers,
and documentation are English; the two languages appear only as content.

### Explicit non-goals

- No purchasing, payment, or order modification. The assistant reads; it never
  writes to commerce state. This keeps the blast radius of a wrong answer to
  misinformation rather than money.
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
orders additionally carry `channel_order_code`, indexed unique and **sparse**.
Website orders have none, and a plain unique index would treat all their missing
values as one colliding null.

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

The system prompt (5,379 characters) and the six tool definitions do not change
between turns, so a cache breakpoint sits at the end of the system block. Tools
render before the system prompt in the cached prefix, so one breakpoint covers
everything stable.

**Nothing volatile may enter that prefix.** A timestamp or a session identifier
in the system prompt invalidates the cache for every conversation at once, and
the only symptom is a bill several times larger. The dashboard reports cache hit
rate specifically so this failure is visible.

### Model tiering

| Task | Model | Why |
|---|---|---|
| Turn classification | `claude-haiku-4-5` | Bounded labelling, runs on every turn |
| Agent | `claude-opus-5` | Tool selection and grounded synthesis |
| Rubric judging | `claude-opus-5` at low effort | A weak judge mistakes fluent wrong answers for correct ones |

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
of the 225-test suite.

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
and waits. Concurrency is bounded by a semaphore, because firing 49 turns at once
produces rate limit errors rather than results.

---

## 11. Deployment

| Layer | Service | Rationale |
|---|---|---|
| Frontend | Azure Static Web Apps (Free) | Static bundle on a CDN |
| Backend | Azure Container Apps | Scales to zero, streams without buffering |
| Database | MongoDB Atlas M0 (Free) | Documents, vectors, and telemetry in one place |

Container Apps rather than App Service or Functions for three specific reasons:
it scales to zero so an idle demonstration costs nothing; it streams
Server-Sent Events without buffering; and it takes a container image directly, so
what runs in Azure is what was built.

The image is built with `az acr build`, in Azure rather than locally. Building on
an Apple Silicon machine and pushing produces an arm64 image that fails with
`exec format error`.

The frontend calls same-origin `/api/*` paths. Static Web Apps rewrites them to
the backend, so no hostname is compiled into the bundle and the same build works
in development and production.

Secrets are Container Apps secrets referenced by `secretref:`, never baked into
the image.

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

**Automated Embedding is a public preview feature.** The `explicit` fallback
exists for this reason but has not been exercised end to end.

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
