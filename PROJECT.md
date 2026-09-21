# Project state

Working notes for this repository. Read this first; it is written so that a
session can pick up the work without scanning the codebase.

Last updated: **2026-09-21**.

---

## 1. What this is

A university capstone (**Đồ án 2**, UIT). The chatbot is the deliverable and it
will be defended in front of a committee, so the written record matters as much
as the code: `docs/architecture.md` is structured to map onto report chapters.

**Objective being satisfied, verbatim:**

> Chatbot hỗ trợ tư vấn sản phẩm và giải đáp về chính sách và hành trình đơn hàng
> trên các sàn TMĐT

Four clauses, each with a home in the code:

| Clause | Where it lives |
|---|---|
| Tư vấn sản phẩm | `search_products`, `get_product_details`, `compare_products` |
| Giải đáp chính sách | `search_policies`, RAG with mandatory `chunk_id` citation |
| Hành trình đơn hàng | `get_order_status` → full status timeline, carrier, ETA |
| Trên các sàn TMĐT | `channel` on every order; see §3, this is the load-bearing one |

## 2. The scenario (settled 2026-09-21; do not relitigate)

Northlight is a fictional Vietnamese electronics retailer selling through **four
channels**: its own website, Shopee, Lazada, TikTok Shop.

The chatbot is **the company's own consolidated support site**. It is not
deployed into marketplace chat windows and calls no marketplace API.

Why this satisfies "trên các sàn TMĐT": each marketplace has its own seller
inbox with no view of the other three, so shoppers get different answers
depending on which app they open, and the seller answers everything four times.
Consolidating that is the problem being solved. The seller already holds its own
order records, which is the position a real seller is in after exporting from
each platform into an order system (Sapo, KiotViet, Haravan).

**Why no real API integration:** Shopee/Lazada Open Platform needs an approved
partner account and per-shop OAuth, unavailable to a student project. It would
also only change where order rows come from, not how the assistant reasons about
them. This is argued in `docs/architecture.md` §1 and listed as a deliberate
scope decision in §12, not as an unfinished feature.

If a committee member asks "which sàn does it integrate with?", the answer is in
`docs/architecture.md` §1 under *Why no marketplace API integration*.

## 3. The multi-channel design

Two facts carry the whole thing.

**An order has two identities.** Internal `order_code` (`DH2026090001`) and
`channel_order_code`, the code the marketplace issued (`250905K7MQ2XPL`). A
shopper who bought on Shopee has only ever seen the second one. `get_order_status`
matches on **either**:

```python
{"$or": [{"order_code": supplied}, {"channel_order_code": supplied}]}
```

The two code spaces are disjoint and each is unique, so one string resolves to at
most one order. Tests assert both properties.

**This does not weaken verification.** Neither code is a credential; the contact
detail is, and it is checked identically whichever key found the row. The wider
key space changes which orders can be addressed, without changing which orders
can be read.
Asserted in three places: `tests/test_seed_data.py`, `seed/smoke.py`, and eval
case `ord-sec-12`.

**Policy answers are channel-dependent.** Return windows, refund destinations and
shipping fees genuinely differ per marketplace. Three policy sections carry this:

- `return-policy#marketplace-returns`: where the request is filed, per platform
- `return-policy#marketplace-refunds`: where the money lands, per platform
- `shipping-policy#marketplace-shipping`: carrier assignment, fees

Warranty deliberately does **not** vary by channel; that is itself an answer
shoppers ask for.

### Fixed demo data

Showcase orders have fixed codes and fixed marketplace codes because the demo and
the eval set quote them. They must not drift.

| Internal | Channel | Marketplace code | Contact | Status |
|---|---|---|---|---|
| DH2026090001 | shopee | `250905K7MQ2XPL` | 0901234567 | out_for_delivery |
| DH2026090002 | lazada | `7012845390127` | binh.tran@example.com | delivered |
| DH2026090003 | website | none | 0987654321 | cancelled |
| DH2026090004 | tiktok_shop | `577483920164523` | 0938111222 | returned |
| DH2026090005 | shopee | `250908R4NHB9TW` | 0977333444 | packing |

## 4. Architecture in brief

```
Angular 22 SPA (Azure Static Web Apps)
   | HTTPS + Server-Sent Events
FastAPI (Azure Container Apps)
   |- classifier      claude-haiku-4-5   intent + safety per turn
   |- orchestrator    claude-opus-5      hand-written tool loop
   |- 6 tools over MongoDB Atlas
   |- guardrails: input screening, citation check, groundedness
   '- telemetry: one event row per turn
MongoDB Atlas M0, Automated Embedding via Voyage AI
```

Non-obvious decisions, each argued at length in `docs/architecture.md`:

- **The tool loop is hand-written**, not the SDK tool runner. The runner hides
  its message history, and this app persists conversations, so history would have
  to be mirrored anyway. The manual loop also allows mid-turn progress events and
  a guardrail check between rounds.
- **Policy chunks are split by hand-authored section**, never by token count. A
  token chunker renumbers everything on edit and rots every citation in the eval
  set. Adding the three marketplace sections proved this out: three new anchors,
  zero existing citations invalidated.
- **Keep chunks near the corpus average (300–900 chars).** The first marketplace
  draft was one 1,242-char chunk; it diluted its own embedding and retrieved
  worse for both questions than the two halves do separately.
- **Contacts are stored only as salted hashes.** A wrong contact and a
  non-existent order code return byte-identical responses, so the tool cannot be
  used to enumerate which order codes exist.
- **`channel_order_code` is indexed unique AND sparse.** Website orders have
  none; a plain unique index would treat every missing value as one colliding
  null.
- **Tool definition order is stable** (`sorted`). The tools block precedes the
  system prompt in the cached prefix, so reordering it silently destroys prompt
  caching and multiplies cost.

## 5. Current state

**Working and verified:** 250 tests pass (`python3 -m pytest -q`, ~0.3s, needs no
database). All modules import. Order generation is deterministic across runs.

**Corpus:** 480 products / 8 categories, 26 policy chunks / 5 documents, 325
orders / 4 channels.

**Eval dataset: 56 cases.** Policy 20, order 12, product 10, guardrail 14.
Seven cover multi-channel behaviour specifically. Five are security cases.

```
backend/
  app/
    config.py  security.py  telemetry.py
    db/        client, schema, indexes, vector search
    agent/     orchestrator.py (the tool loop), classifier.py, guardrails.py
               prompts/system_prompt.md, tools/{products,policies,orders}.py
    api/       chat.py (SSE), admin.py, health.py
  seed/        catalog, orders, policies/*.md, parse_policies, load, smoke
  evals/       dataset/*.jsonl, scoring, judges, run_eval, report
  tests/
frontend/src/app/{chat,admin}/
infra/         Azure deploy scripts, docker compose
```

### Commands

```bash
cd backend
python3 -m pytest -q             # 250 tests, no database needed
python3 -m seed.orders           # inspect orders + channel split
python3 -m seed.parse_policies   # inspect the 26 chunks
python3 -m seed.load             # write everything to MongoDB, build indexes
python3 -m seed.smoke            # verify retrieval WITHOUT calling Anthropic
python3 -m evals.run_eval --limit 5   # costs a few cents, prompts first
```

`seed.smoke` exists because a retrieval failure and a model failure look
identical from the chat window. Run it before blaming the model.

## 6. What is left

Ordered. Item 1 is by far the most valuable.

1. **Run the full evaluation and commit the results.** `backend/evals/results/`
   does not exist. The harness is complete but has never produced a number. The
   report needs that table: intent routing, recall@3/@5, MRR, groundedness,
   security pass rate, cost and latency. Needs `ANTHROPIC_API_KEY`, a seeded
   Atlas cluster, and a few dollars. **This is the single biggest gap.**
2. **Authenticate the admin router.** `app/api/admin.py` has no gate; it exposes
   operating cost and quotes shopper messages. Blocked on a decision: shared
   secret header vs. Azure Container Apps auth. Touches `infra/deploy-*.sh`.
3. **Channel breakdown on the dashboard.** Orders and turns per channel. The data
   is already there (`orders.channel` is indexed); only the aggregation in
   `admin.py` and the Angular component are missing. Good demo material.
4. **Multi-turn eval cases.** Every case is currently one message with empty
   history, so reference resolution ("so sánh cái này với cái kia") is untested,
   and it is the most likely real failure mode.
5. Conversation summarisation past the 12-turn window; a reranker over fused
   results; load testing.

### Known limitations (all stated in `docs/architecture.md` §12 on purpose)

Raising these first is a defense strategy, not an oversight. Keep them in the
report.

- No live marketplace integration (a scope decision; see §2 above). The untested
  part is the ingestion path: reconciling exports, platform status vocabularies,
  and codes that change after a split shipment.
- All data is synthetic.
- Automated Embedding is a public preview feature; the `EMBEDDING_MODE=explicit`
  fallback exists for that reason but has never been exercised end to end.
- Numeric grounding is a textual heuristic. It produces false positives when the
  model correctly rounds or restates a figure, so it is reported, never enforced.
- Conversation memory is a fixed 12-turn window with no summarisation.
- Single-turn evaluation only.
- No load testing. Latency figures come from eval runs against an idle free-tier
  cluster.

## 7. Conventions

- **All code, comments, identifiers and documentation in English.** Vietnamese
  appears only as content: policy text, product names, chat.
- Bilingual fields sit side by side (`name_vi` / `name_en`), never a nested
  language map, because Atlas Search filters and sorts read them directly.
- Every searchable document carries one `embedding_source` field combining both
  languages, so a Vietnamese and an English query hit the same vector.
- Comments explain why rather than what. Match the surrounding density: this
  codebase comments decisions and trade-offs, not mechanics.
- Seed generation is deterministic (fixed `SEED`, fixed `NOW`). Re-running must
  reproduce identical data or the eval set breaks.
- Policy sections carry hand-authored slugs. **Never renumber or rename an
  existing one.** Each is a citation anchor referenced by the eval dataset.

## 8. Changelog

**2026-09-21, multi-channel consolidation.** Settled the scenario in §2 and
implemented it. `channel` + `channel_order_code` on `Order`; lookup by either
code; sparse unique index; three new per-channel policy sections; system prompt
and tool descriptions updated; 11 new tests; 6 new smoke checks; 7 new eval cases
(56 total). Reframed `README.md` and `docs/architecture.md` §1 around the
consolidated-support-site scenario, and documented the two-key design in §7.
Fixed stale docs: eval count said 47 (was really 49, now 56), chunk count said 23
(now 26), and `docs/setup.md` demoed `Tìm áo khoác nam dưới 500k`, a men's
jacket left over from before the store became an electronics retailer.
Uncommitted at time of writing.
