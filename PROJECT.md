# Project state

Working notes for this repository. Read this first; it is written so that a
session can pick up the work without scanning the codebase.

Last updated: **2026-09-22** (overnight session). Keep this file current with every change.

---

## 1. What this is

A university capstone (**Đồ án 2**, UIT). The chatbot is the deliverable and it
will be defended in front of a committee, so the written record matters as much
as the code: `docs/architecture.md` is structured to map onto report chapters.

**Deadline: the defense is within about one month of 2026-09-21.** Every choice
below is weighed against that. A working, measured system beats a larger one.

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

## 2. Settled decisions (do not relitigate)

### The scenario (settled 2026-09-21)

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
them. Argued in `docs/architecture.md` §1, listed as a deliberate scope decision
in §12.

### Platform and model provider (settled 2026-09-21)

**Hybrid architecture.** The live chatbot keeps **MongoDB Atlas** (hosted on
Azure) as its operational database. **Snowflake** is added as an **analytics
layer** on top (see §6), not as a replacement.

**Superseded the same day, 2026-09-21: the chatbot's model is `gpt-5-mini` on
Azure OpenAI (Foundry), paid from the Azure for Students credit.** The Cortex plan
failed: Snowflake blocks the COMPLETE function on trial accounts (see §5,
*BLOCKER*). Paying for the Anthropic API was declined. Azure's own GPT models are
billed as ordinary Azure usage, so the student credit covers them, and quota was
verified before committing (§5, *Setup progress*). The Anthropic and Cortex
providers stay in code as alternatives behind `LLM_PROVIDER`.

Consequences to keep in mind: `gpt-5-mini` is a smaller model than Claude Opus 5,
and the system prompt and tool descriptions were written for Claude, so answer
quality must be measured by the evaluation, not assumed. The Anthropic-specific
sections of `docs/architecture.md` (§6, §9) must be rewritten around a
provider-neutral design.

Why, and what was ruled out:

- **Claude on Azure (Microsoft Foundry): impossible here.** Microsoft's docs
  exclude subscriptions without a pay-as-you-go billing method, naming student
  and credit-only accounts. The $100 student credit cannot pay for Claude at all.
- **Anthropic API directly:** works, needs its own prepaid billing (a few
  dollars). Kept as the fallback.
- **Azure's own GPT models via Foundry:** payable from student credit, but means
  rewriting the ~760-line model layer, and student quota was never verified.
- **Full migration to Snowflake:** rejected. It means rewriting the 1,478-line
  data layer as well as the model layer, inside the defense month. A warehouse
  is also the wrong shape for per-request order lookups and conversation writes.
- **No AI (rule-based):** rejected. It would void most of the design record.
- **Why MongoDB, not an Azure-native database:** the code relies on
  Atlas-only features (`$vectorSearch` ×5, `$search` ×4, and Automated
  Embedding, which generates vectors for free with no embedding code). Cosmos DB
  would mean building and paying for embeddings. The committee answer: *"The
  database runs on Azure; Atlas gives vector search, full-text search and
  automatic embeddings together on the free tier."*

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
can be read. Asserted in three places: `tests/test_seed_data.py`,
`seed/smoke.py`, and eval case `ord-sec-12`.

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
   |- classifier      gpt-5-mini (minimal reasoning)   intent + safety per turn
   |- orchestrator    gpt-5-mini (medium effort)       hand-written tool loop
   |     '- via app/agent/llm.py: Azure OpenAI (in use) | Anthropic | Cortex
   |- 6 tools over MongoDB Atlas
   |- guardrails: input screening, citation check, groundedness
   '- telemetry: one event row per turn
MongoDB Atlas M0 on Azure, Automated Embedding via Voyage AI
```

Non-obvious decisions, each argued at length in `docs/architecture.md`:

- **One provider-neutral model interface: `app/agent/llm.py`.** The agent loop,
  classifier and judge call it; it has an Anthropic-format backend (Anthropic and
  Cortex) and an OpenAI-format backend (Azure OpenAI). Each keeps its native
  message list across tool rounds. The OpenAI side reassembles streamed
  tool-call argument fragments by index, sends `strict` only for schemas where
  every property is required (the product search's optional filters aren't),
  flags truncated JSON arguments as malformed instead of guessing, and closes
  every stream in a context manager.
- **`python -m app.agent.probe`** checks the configured provider through that same
  interface: a streamed round, a full tool round trip, and structured output with
  the real classifier and judge schemas. On `gpt-5-mini` every check passes.
  `LLM_STRICT_TOOLS` and `LLM_EFFORT` switch off the two features a provider may
  reject.
- For Cortex: don't pass an `httpx.Client` as Snowflake's docs show; anthropic
  1.x is built on `httpx2` and rejects it. Use `default_headers`.
- **Secrets are declared `SecretStr` settings.** pydantic-settings loads `.env`
  into declared fields only, never into `os.environ`. `ANTHROPIC_API_KEY` used to
  be undeclared, so a key in `.env` never reached the SDK locally (Azure hid the
  bug by injecting real env vars). Fixed 2026-09-21, with a regression test.
- **The tool loop is hand-written**, not the SDK tool runner. The runner hides
  its message history, and this app persists conversations, so history would have
  to be mirrored anyway. The manual loop also allows mid-turn progress events and
  a guardrail check between rounds.
- **Policy chunks are split by hand-authored section**, never by token count. A
  token chunker renumbers everything on edit and rots every citation in the eval
  set.
- **Keep chunks near the corpus average (300–900 chars).** A 1,242-char chunk
  diluted its own embedding and retrieved worse than its two halves.
- **Contacts are stored only as salted hashes.** A wrong contact and a
  non-existent order code return byte-identical responses, so the tool cannot be
  used to enumerate which order codes exist.
- **`channel_order_code` is indexed unique with a PARTIAL filter
  (`$type: "string"`), not sparse.** Website orders store an explicit null, and
  sparse skips only *absent* fields, so a sparse unique index rejected the second
  website order and failed the first real seed. A regression test pins this.
- **Atlas connections use certifi's CA bundle** (`app/db/client.py`,
  `mongodb+srv` URIs only). The python.org macOS Python ships no CA store, so
  PyMongo could not verify Atlas.
- **Automated Embedding index fields need `"modality": "text"`** since the
  preview API changed; without it Atlas rejects the index.
- **M0 allows only 3 search indexes per cluster**, and this app needs exactly 3
  (`products_vector`, `products_text`, `policies_vector`). Don't load Atlas's
  sample dataset on this cluster: it takes a slot. (It was dropped 2026-09-21.)
- **Tool definition order is stable** (`sorted`). The tools block precedes the
  system prompt in the cached prefix, so reordering it silently destroys prompt
  caching and multiplies cost.

## 5. Current state

**Working and verified:** 363 backend tests pass (`.venv/bin/python -m pytest -q`,
~1 s, no database or network). The Angular app builds (`npx ng build`) and every
page was driven with Playwright against the real backend and model, including
the full loop: shop → cart → checkout → order created → "track with the
assistant" → route map shown.

**Real services, 2026-09-21:** model provider passes every probe check on
`gpt-5-mini`; Atlas holds the seeded data (480 products, 26 policy chunks, 325
orders) with `products_vector`, `products_text` and `policies_vector` READY;
`seed.smoke` passes all 24 checks.

**Evaluation (see `docs/evaluation.md`):** first full run 66% answer pass rate;
after a judge calibration and fixes, **94.6–98.2%** across two identical final
runs (quote the range), policy recall@5 87.5–97.5% (run-to-run variance in the
model's search queries), tool selection, groundedness and security 100%, 0
errors, about 0.12–0.13 USD per full run and ~13 s per turn. Eval spend to date
is about 1.05 USD of the 3 USD the developer allowed.

**Features added 2026-09-21 (night):** order route map (warehouse → hubs →
destination province, `app/geo.py`, `order-map.component`); storefront with
catalogue, product page showing which channels list the product, cart, guest
checkout with demo payment (`app/api/shop.py`, `frontend/src/app/shop/`);
content-filter rejections become polite refusals; reply language stated per turn.

**Known cosmetic issue:** at process exit, Python 3.14 with `httpcore2` logs
*"generator didn't stop after athrow()"* for OpenAI streams. It reproduces with a
minimal script that closes both stream and client properly, so it is a library
issue; it fires only at shutdown and affects no request.

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
               llm.py (provider-neutral model interface), client.py (SDK clients),
               probe.py (provider check)
               prompts/system_prompt.md, tools/{products,policies,orders}.py
    api/       chat.py (SSE), admin.py, health.py
  seed/        catalog, orders, policies/*.md, parse_policies, load, smoke
  evals/       dataset/*.jsonl, scoring, judges, run_eval, report
  tests/
frontend/src/app/{chat,admin}/
infra/         Azure deploy scripts, docker compose
  snowflake/setup.sql   one-time Cortex credential setup
```

### Commands

```bash
cd backend
.venv/bin/python -m pytest -q             # 264 tests, no database needed
.venv/bin/python -m app.agent.probe       # which features the model provider accepts
.venv/bin/python -m seed.orders           # inspect orders + channel split
.venv/bin/python -m seed.parse_policies   # inspect the 26 chunks
.venv/bin/python -m seed.load             # write everything to MongoDB, build indexes
.venv/bin/python -m seed.smoke            # verify retrieval WITHOUT calling the model
.venv/bin/python -m evals.run_eval --limit 5   # costs model credits, prompts first
```

`seed.smoke` exists because a retrieval failure and a model failure look
identical from the chat window. `app.agent.probe` does the same for provider
incompatibilities. Run both before blaming the model.

### Setup progress (the developer's machine and accounts)

| Item | State |
|---|---|
| Azure CLI | Installed (Homebrew). Signed in to the **UIT directory** with `az login --tenant 2dff09ac-2b3b-4182-9953-2b548e0d0b39 --use-device-code`. A plain `az login` finds **0 subscriptions**. |
| Azure subscription | **Azure for Students**, enabled, $100 credit, expires 2027-04-22 |
| Allowed regions | **Only** `koreacentral`, `japanwest`, `indonesiacentral`, `japaneast`, `eastasia`. `southeastasia`, the deploy script's default, is **blocked** by policy. Use `LOCATION=eastasia`. |
| Resource providers | `Microsoft.App`, `OperationalInsights`, `ContainerRegistry`, `CognitiveServices` registered; `Microsoft.Web` was still registering |
| Resource group | `rg-uit-chatbot` in `eastasia` (created 2026-09-21) |
| Azure OpenAI | Resource **`uit-chatbot-ai-6435`** (kind AIServices, S0) in **`japaneast`**. Endpoint `https://uit-chatbot-ai-6435.openai.azure.com/openai/v1/`. Deployment **`gpt-5-mini`** (version 2025-08-07, GlobalStandard, capacity 100 = **100K tokens/min, 100 requests/min**). Smoke-tested OK. Key via `az cognitiveservices account keys list -g rg-uit-chatbot -n uit-chatbot-ai-6435`; never commit it. |
| Azure OpenAI quota | Student subscription, GlobalStandard: **only `gpt-5-mini` (500) and `o4-mini` (100)**; every other GPT model has 0. Same in `koreacentral`. `eastasia` offers provisioned capacity only; `japanwest` and `indonesiacentral` offer none. |
| gpt-5-mini price | Azure retail, GlobalStandard: **$0.25 input / $0.025 cached input / $2.00 output per 1M tokens**. Meters marked `pp` are priority processing (higher), not what we use. Estimated < 1 cent per chat turn. |
| CLI extensions | `containerapp` installed. `staticwebapp` and the `swa` CLI not installed yet |
| Network | On an **iPhone hotspot whose IPv6 is broken**: TCP to Microsoft IPv6 hangs, and Python-based tools (the Azure CLI) freeze. IPv6 is currently **off** (`networksetup -setv6off Wi-Fi`). Turn it back on after deploying with `networksetup -setv6automatic Wi-Fi`. |
| MongoDB Atlas | M0 cluster on Azure, network access `0.0.0.0/0`. Connection string in `.env`. Seeded 2026-09-21. The Atlas sample dataset (`sample_mflix`) was dropped to free a search-index slot |
| Snowflake | **Trial started 2026-09-21 → ends about 2026-10-21** (30 days, or sooner if the free credits run out). Account on **Azure**. `infra/snowflake/setup.sql` has been run. The first token was exposed in a chat, so it is being replaced (`REMOVE` then `ADD PROGRAMMATIC ACCESS TOKEN`); tokens issued by the script expire after 45 days. Sign-in: an activation email creates the username and password; use **Forgot password?** if stuck. Region: Claude has no in-region Snowflake host, so it always runs cross-region (`ANY_REGION`) |
| `backend/.env` | Exists (git-ignored, mode 600). `LLM_PROVIDER=azure_openai`, endpoint + key for `uit-chatbot-ai-6435`, `AGENT_MODEL=CLASSIFIER_MODEL=gpt-5-mini`, `AGENT_EFFORT=medium`, gpt-5-mini `PRICE_*`, `MONGODB_URI` filled. The Snowflake values are left over and unused |

### RESOLVED (2026-09-21): Automated Embedding was throttled to 3 queries/min

Each vector search embeds the query through Atlas's Voyage integration. On an M0
cluster **with no payment method on the Atlas organization**, query-time embedding
is limited to **3 requests / 2,000 tokens per minute** per model, so the fourth
search in a minute fails. That breaks `seed.smoke`, the eval and any live demo.
With a payment method on file the same free M0 gets **2,000 requests/min**, and
Atlas gives **200M free tokens per model per organization** (we use far under 1M),
so the realistic charge is $0.

**Decision (2026-09-21): option A, add a payment method in Atlas** (Organization →
Billing → Add Payment Method); no code change. A debit card was added; the organization moved to **Usage tier 1** (shown on
Atlas's *Increase Rate Limit* page) and the higher limit applied within about ten
minutes. Smoke then passed in full. Keep the card on the organization: removing it
drops the cluster back to 3 queries/min and the demo breaks. Fallback if ever needed:
option B, `EMBEDDING_MODE=explicit` with Azure OpenAI `text-embedding-3-small`
(quota GlobalStandard 1000 in japaneast), which needs an Azure embedding path in
`app/db/vector.py`, 1536-dim indexes and a re-seed.

### BLOCKER (2026-09-21): the Snowflake account cannot use the Cortex REST API

Every Cortex REST call from the trial account returns **HTTP 403, code 003001,
"This account is not allowed to access this endpoint. Please contact Snowflake
support."** Tested: the Messages endpoint, Chat Completions and the older
`inference:complete`, with Claude models *and* `llama3.1-8b`. Authentication
succeeds (a bad token gives 401), and the service user's role holds
`SNOWFLAKE.CORTEX_USER`, the documented prerequisite. The block is account-level.
The REST API docs mention no trial restriction. **Confirmed cause:** running
`SELECT SNOWFLAKE.CORTEX.COMPLETE(...)` in Snowsight returns *"AI function COMPLETE
is not available for trial accounts."* The language-model function is blocked on
trials altogether, through SQL and REST alike. The trial page's "roughly ten
credits a day of Cortex AI Functions" does not cover COMPLETE.

Consequence: **Cortex cannot power the chatbot on a trial account.** The code is
correct and stays in place (the switch and the probe also work for Anthropic).
Whether other Cortex AI SQL functions (e.g. sentiment or classification) work on
the trial, for the analytics module, is untested. Plain SQL analytics in
Snowflake is unaffected. Choosing the chatbot's provider is open again (see §6,
week 1).

Also open: the token in `backend/.env` is still the one exposed in a chat. It
authenticates, so it was not replaced. Remove it (`ALTER USER CHATBOT_SVC REMOVE
PROGRAMMATIC ACCESS TOKEN CHATBOT_BACKEND;`) whatever is decided.

### Fixed: the frontend deploy (2026-09-22)

Static Web Apps cannot rewrite `/api` to an external URL on the Free plan. The
bundle now reads the backend URL at runtime from `config.js` (empty in dev, where
the Angular proxy handles `/api`; written by `infra/deploy-frontend.sh` in
production) and calls the backend directly; the backend admits the site through
`CORS_ORIGINS`. `docs/architecture.md` §11 updated.

**ACR Tasks are refused on Azure for Students** (`TasksOperationsNotAllowed`), so
`az acr build` fails. `BUILD_MODE=local` in `deploy-backend.sh` builds with the
local Docker Desktop, pinned to `linux/amd64`, and pushes. Docker must be running.

## 6. What is left

### Plan for the month

**Week 1: running, deployed, measured.**
0. ~~Add an Azure OpenAI provider~~ **done 2026-09-21**: `app/agent/llm.py`, probe
   passing, 27 new tests, deploy script and docs updated. Uncommitted.
1. ~~Sign up for the Snowflake trial~~ (done; Cortex unusable on the trial). Originally: sign up for the Snowflake trial (Azure, an Asian region), run
   `infra/snowflake/setup.sql`, and put `SNOWFLAKE_ACCOUNT_URL` and `SNOWFLAKE_PAT`
   into `.env`. Run `app.agent.probe` and copy the switch values it prints.
2. ~~Put the Atlas connection string into `.env`, run `seed.load`~~ (done; three
   bugs fixed on the way, see §4). `seed.smoke` passes in full (after adding a
   payment method to Atlas to lift the embedding rate limit, see §5).
3. ~~Run the chatbot locally end to end~~ (done 2026-09-21, see §5).
4. ~~Run the full evaluation~~ (done 2026-09-22; `docs/evaluation.md` holds the
   run history and the numbers for the report. Per-run output in
   `backend/evals/results/` is git-ignored on purpose).
5. Deploy: see the DEPLOY STATUS line in §5 for where it stands.
6. Added on request 2026-09-21: order route map; storefront with cart and guest
   checkout (demo payment, no accounts). Done.

**Decisions the developer made on 2026-09-21 (night):** payment is demo only (no
gateway); guest checkout only (no accounts); the map is shown for every verified
order. Open question for the developer: show the map only for website and
seller-shipped orders, with platform-shipped orders showing status only?

**Weeks 2–3: Snowflake analytics module (additive; the chatbot must keep working
without it).** Export conversations, telemetry and orders from MongoDB to
Snowflake, optionally landing files in Azure Data Lake Storage first. Then:
customer demand (what is asked for but not stocked), questions by channel,
sentiment and complaint themes via Cortex AI SQL functions, and possibly
natural-language questions over the data (Cortex Analyst) for a manager view.
This also realises extension 6 in `docs/architecture.md` §13 (operational /
analytical split). Not designed yet.

**Week 4:** report, demo rehearsal, and a backup plan: switching
`LLM_PROVIDER=anthropic` if the trial runs out.

### Backlog (after the above)

1. **Authenticate the admin router.** `app/api/admin.py` has no gate; it exposes
   operating cost and quotes shopper messages. Undecided: shared-secret header vs.
   Azure Container Apps auth.
2. **Channel breakdown on the dashboard.** `orders.channel` is indexed; only the
   aggregation in `admin.py` and the Angular component are missing.
3. **Multi-turn eval cases.** Every case is one message with empty history, so
   reference resolution ("so sánh cái này với cái kia") is untested.
4. Conversation summarisation past the 12-turn window; a reranker; load testing.

### Known limitations (all stated in `docs/architecture.md` §12 on purpose)

Raising these first is a defense strategy, not an oversight. Keep them in the
report.

- The model is `gpt-5-mini` (student-credit quota), smaller than the Claude model
  the prompts were first written for. The eval measures the result.
- No live marketplace integration (a scope decision; see §2). The untested part
  is the ingestion path: reconciling exports, platform status vocabularies, and
  codes that change after a split shipment.
- Marketplaces increasingly mask buyer phone/address from sellers; for those
  orders "code + phone" could not verify, and a real deployment needs another
  second factor. Platforms also discourage moving buyers off-platform.
- All data is synthetic. The storefront is a demo: no accounts, no payment,
  province-level delivery, no restocking of cancelled orders.
- Automated Embedding is a public preview feature and needs a payment method on
  the Atlas organisation for a usable query rate; the `EMBEDDING_MODE=explicit`
  fallback has never been exercised end to end.
- Numeric grounding is a textual heuristic, reported, never enforced.
- The eval judge cannot see tool data, so it can mark down a correct spec
  (`prd-en-04`).
- Conversation memory is a fixed 12-turn window with no summarisation.
- Single-turn evaluation only. No load testing.

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
- **Secrets never go in chat, in this file, or in git.** They live only in
  `backend/.env` (git-ignored) and in Azure Container Apps secrets. This file is
  pushed to GitHub, so keep account identifiers out of it unless they are needed
  to resume work.
- **Update this file with every change**, in the same commit.

## 8. Changelog

**2026-09-22 (overnight), evaluation, map, storefront, deploy fixes.** First full
eval run (66% pass); judge calibrated (re-grading the same answers: 91%), then
fixes for every genuine fault: content-filter 400 as a refusal, per-turn reply
language, classifier categories/complaints/credentials (one regression caught
and fixed), search-first product advice, warranty-on-every-channel sentence,
refund search filter, out-of-scope reply naming categories. Final: 94.6–98.2%
pass over two identical runs, all in `docs/evaluation.md`. Added the order route map
(`app/geo.py`, `order-map.component`, Natural Earth SVG with Hoàng Sa and
Trường Sa) and the storefront (catalogue with channel listings, cart, guest
checkout, confirmation handing the order to the assistant). Fixed the frontend
deploy (runtime `config.js` + CORS) and added `BUILD_MODE=local` for the
student subscription's ACR Tasks block. Committed in logical commits, not pushed.

**2026-09-21, first end-to-end run; two chat UI fixes.** Atlas payment method
added, lifting the embedding limit; `seed.smoke` passes all 24 checks. Real turns
through the API and the browser all worked. Fixed in the chat UI: raw
`[ref:...]` markers were printed inside replies (now stripped for display,
including a half-streamed marker; sources still show as chips), and finished tool
steps kept their "Đang…" (in progress) label (each tool now has a finished label,
"Đã…"). Uncommitted.

**2026-09-21, Azure OpenAI provider; first real seed.** Cortex proved unusable on a
Snowflake trial, so the model moved to `gpt-5-mini` on Azure OpenAI (resource
`uit-chatbot-ai-6435`, Japan East), paid from student credit, after checking
quota region by region. Added `app/agent/llm.py` (provider-neutral interface over
Anthropic and OpenAI wire formats) and rewired the orchestrator, classifier and
judge onto it; rewrote the probe around it (tool round trip included); 27 new
tests, mutation-checked; deploy script gained an `azure_openai` branch and now
takes model names from `.env` (they were hard-coded to Claude). Seeding Atlas for
the first time exposed and fixed: the sparse-vs-partial index bug, `seed.load`
closing its client in a second event loop (it failed on every run), the missing
`modality` field for Automated Embedding, missing CA certificates for Atlas, and
the M0 three-search-index limit (sample dataset dropped). `docs/architecture.md`
§4, §9 (*Model tiering*, *Model provider*), §11 and §12 rewritten; README and
`docs/setup.md` updated. Uncommitted. First `seed.smoke` hit Atlas's 3-queries/min embedding
limit for organizations without a payment method; a card was added (Usage tier 1)
and smoke now passes all 24 checks.

**2026-09-21, Snowflake Cortex as model provider.** Settled the hybrid platform
decision (§2). `LLM_PROVIDER` switch in `app/config.py`; `app/agent/client.py`
builds the Cortex client; `strict` tools and `effort` made switchable;
`app/agent/probe.py` added; `infra/snowflake/setup.sql` (service user, Cortex-only
role, any-IP network policy, 45-day token, cross-region inference);
`infra/deploy-backend.sh` sends only the chosen provider's secret and rejects
template placeholders (tested with `az` stubbed). Fixed the `.env` key bug
(§4). 14 new tests (264 total). Documented in README, `docs/setup.md`, and
`docs/architecture.md` §2, §9 (*Model provider*), §11, §12. Also recorded the
Azure setup findings above. Uncommitted at time of writing.

**2026-09-21, product card redesign** (`1227c58`). Category line glyphs for all
eight categories instead of two-letter brand tiles; discount badge pinned to the
card corner (inline, it made card rows wrap raggedly); stale `theme-color`
corrected. Verified by rendering at card size in both themes, and in the running
app with Playwright.

**2026-09-21, multi-channel consolidation** (`9229503`, `c3b0397`). Settled the
scenario in §2 and implemented it. `channel` + `channel_order_code` on `Order`;
lookup by either code; sparse unique index; three new per-channel policy
sections; system prompt and tool descriptions updated; 11 new tests; 6 new smoke
checks; 7 new eval cases (56 total). Reframed `README.md` and
`docs/architecture.md` §1 around the consolidated-support-site scenario, and
documented the two-key design in §7. Fixed stale docs: eval count said 47 (was
really 49, now 56), chunk count said 23 (now 26), and `docs/setup.md` demoed
`Tìm áo khoác nam dưới 500k`, a men's jacket left over from before the store
became an electronics retailer.
