# Northlight Support Chatbot

A customer-support chatbot for **Northlight**, a fictional Vietnamese consumer
electronics retailer. The catalogue covers phones, tablets, laptops, audio,
wearables, televisions, home appliances, and charging accessories, in the mould
of Thế Giới Di Động or FPT Shop.

Northlight sells through four channels: its own website, and the Shopee, Lazada,
and TikTok Shop marketplaces. Support is not handled on those marketplaces. Each
one has its own seller chat, its own inbox, and no view of the other three, so a
shopper asking "where is my order?" gets a different answer depending on where
they happen to ask, and the seller answers the same questions four times over.

This chatbot is the company's own consolidated support site, answering for orders
from every channel in one place. That is the case for building it: most shoppers
asking about an order did not buy on the site they are asking on. No marketplace
API is integrated, and none is required. The seller already holds its own order
records, which is the position a real seller is in after exporting from Shopee,
Lazada and TikTok Shop into a single order system.

Electronics rather than general merchandise on purpose: warranty terms, return
windows, and spec comparison all carry real weight on expensive goods, so all
three jobs below have something substantial to work with.

It covers three jobs:

1. **Product consultation** — semantic and keyword search over a product catalogue, with filters on category, price, and rating.
2. **Policy questions** — retrieval-augmented answers over store policy documents, where every claim cites the passage it came from. Return windows, refund destinations, and shipping fees differ per channel, and the answer has to follow the channel the order came from.
3. **Order tracking** — status and delivery timeline lookups across all four channels, accepting either the store's own order code or the marketplace's, gated behind identity verification. A verified order's route (warehouse, sorting hubs, destination province) is drawn on a map of Vietnam in the chat.

Around the chatbot, the website is a small **storefront**: a catalogue showing which channels list each product, a cart, and a guest checkout with demo payment. An order placed there is an ordinary website order, so the demo closes the loop: buy on the site, then ask the assistant where the order is.

The chatbot answers in **Vietnamese and English**. All source code, comments, and documentation are in English; both languages appear only as content.

## Architecture

```
Angular 22 SPA  (Azure Static Web Apps)
      |  HTTPS, Server-Sent Events
      v
FastAPI  (Azure Container Apps)
      |- Turn classifier      gpt-5-mini, minimal reasoning
      |- Agent orchestrator   gpt-5-mini, hand-written tool loop
      |     '- Azure OpenAI (in use), Anthropic, or Snowflake Cortex (LLM_PROVIDER)
      |- Six tools over MongoDB
      |- Guardrails and groundedness checks
      '- Telemetry
      |
      v
MongoDB Atlas M0  (Automated Embedding, Voyage AI)
```

## Design decisions worth knowing

**The agent loop is hand-written, not delegated to the SDK tool runner.** The runner keeps its own message history and does not expose it, so an app that persists conversations has to mirror history anyway. A manual loop also lets the backend emit progress events mid-turn and run a guardrail check between rounds.

**An order is reachable by either of its two codes.** A marketplace issues its own order code and never shows the shopper the seller's internal one, so requiring the internal code would make order tracking useless for most orders. Each order therefore stores `channel` and `channel_order_code` alongside `order_code`, and lookup matches on either, as real multi-channel order systems do. The second key is a lookup convenience and does not weaken verification, which the smoke checks and a dedicated evaluation case both assert.

**Order codes alone never unlock an order.** Codes are short and get shared in screenshots. `get_order_status` requires a matching phone number or email, compares salted hashes, and returns an identical response whether the code is wrong or the contact is wrong, so it cannot be used to test which codes exist. The database stores no readable contact details.

**Policy answers are never generated from model memory.** Return windows and fee thresholds are business facts that change. The model must retrieve a passage and cite its `chunk_id`.

**Policy chunks are split by hand-authored section, not by token count.** A token-based chunker renumbers chunks whenever text is edited, which would rot every citation in the evaluation dataset.

**The model provider is pluggable.** `LLM_PROVIDER=azure_openai` (in use) calls a `gpt-5-mini` deployment on Azure OpenAI, paid from the Azure for Students credit; `anthropic` calls Claude directly; `cortex` calls Claude through Snowflake Cortex. The agent loop, classifier and judge talk to one provider-neutral interface in `app/agent/llm.py`, which translates between Anthropic's and OpenAI's tool-calling formats. Claude on Azure is closed to student subscriptions, and Cortex's language model is closed to Snowflake trials; both were verified, and `docs/architecture.md` §9 records how. `python -m app.agent.probe` checks a provider end to end, including a full tool round trip, before any shopper depends on it.

**Embedding is pluggable.** `EMBEDDING_MODE=auto` lets Atlas generate and sync Voyage vectors. `explicit` computes them in this codebase instead. Automated Embedding is in public preview, and this switch is the fallback. Query code is identical either way.

## Repository layout

```
backend/
  app/
    config.py            Settings from environment
    security.py          Contact hashing for order verification
    telemetry.py         One event row per turn
    db/                  Client, schema, indexes, vector search
    agent/
      orchestrator.py    The hand-written tool loop
      classifier.py      Fast per-turn intent and safety labelling
      guardrails.py      Input screening, citations, grounding
      prompts/           System prompt
      tools/             The six tools
    api/                 chat (SSE), admin, health
  seed/                  Catalogue, orders, policy documents, smoke checks
  evals/                 Dataset, scoring, judge, runner, reports
  tests/
frontend/
  src/app/chat/          Chat page and streaming client
  src/app/admin/         Operations dashboard
infra/                   Azure deployment scripts (backend, frontend, analytics job, GitHub OIDC)
docs/                    Setup guide
```

## Setup

Requires Python 3.12 or later and a MongoDB Atlas cluster.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env      # then set MONGODB_URI and the model provider's credentials
```

### Creating the Atlas cluster

1. Create a free M0 cluster at <https://cloud.mongodb.com>, hosted on Azure, region Southeast Asia.
2. Under **Network Access**, add your current IP address. Without this the connection hangs rather than failing clearly.
3. Under **Database Access**, create a user and copy the connection string into `MONGODB_URI`.

### Seeding

```bash
.venv/bin/python -m seed.load
```

This generates the catalogue, orders, and policy chunks, writes them to MongoDB, creates the indexes, and waits until the search indexes are queryable. Generation is deterministic, so re-running reproduces identical data and any SKU or citation referenced by the evaluation set stays valid.

For a demo, `python -m seed.activity` adds 30 days of realistic store activity (chats in Vietnamese and English, telemetry, staff tickets, website orders) so the Operations dashboard and the analytics extract have something to show. Every document it writes is marked `is_simulated: true`; `--clear` removes exactly those.

## Development

Start the API from one terminal:

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Start the Angular client from a second terminal:

```bash
cd frontend
npm install
npm start
```

The client is available at <http://localhost:4200>. Its development proxy forwards
`/api` requests to FastAPI on port 8000. The backend health check is available at
<http://localhost:8000/health>.

Backend checks and local data inspection:

```bash
.venv/bin/python -m pytest          # tests that need no database
.venv/bin/python -m seed.catalog    # inspect the generated catalogue
.venv/bin/python -m seed.orders     # inspect the generated orders
.venv/bin/python -m seed.parse_policies
```

Build the frontend for deployment with `npm run build` from `frontend/`.

### Backend container

With Docker Desktop or another Docker daemon running, build and run the API
container from `backend/`:

```bash
docker build -t uit-ecommerce-chatbot-backend .
docker run --env-file .env -p 8000:8000 uit-ecommerce-chatbot-backend
```

The container listens on port 8000 and exposes the same `/health` and `/ready`
probes used by the local server.

### Checking the model provider

Whichever provider `.env` selects, check it before anything else:

```bash
.venv/bin/python -m app.agent.probe
```

It runs a streamed round, a full tool round trip, and structured output with the real classifier and judge schemas, and prints the `LLM_STRICT_TOOLS` and `LLM_EFFORT` values to put in `.env`. For `LLM_PROVIDER=cortex`, first run [infra/snowflake/setup.sql](infra/snowflake/setup.sql) once in a Snowsight worksheet (a paid Snowflake account is required).

### Verifying retrieval without the model

Retrieval failures and model failures look identical from the chat window. This
separates them, and makes no Anthropic calls:

```bash
.venv/bin/python -m seed.smoke
```

It checks that Vietnamese and English queries both return results, that the price
and rating filters are actually enforced, that each policy probe retrieves the
chunk it should, and that order verification refuses a wrong contact, a
four-digit fragment, and an unknown order code identically.

## Evaluation

A 56-case bilingual dataset covering policy questions, product consultation,
order tracking, multi-channel lookups, and adversarial input.

```bash
cd backend
.venv/bin/python -m evals.run_eval --limit 5   # smoke test, a few cents
.venv/bin/python -m evals.run_eval             # full run
.venv/bin/python -m evals.run_eval --no-judge  # labels only, nearly free
```

Every run costs money, so the runner prints an estimate and waits for
confirmation. Results are written to `evals/results/` (git-ignored) as JSON and
as a Markdown table ready to paste into a report. The runs so far, and what each
fix changed, are recorded in [docs/evaluation.md](docs/evaluation.md). A full run
costs about 0.12 USD on `gpt-5-mini`.

Measured:

| Axis | How |
|---|---|
| Intent routing | Classifier label against a hand-labelled expectation |
| Policy retrieval | recall@3, recall@5, and MRR against gold chunk ids |
| Tool selection | Whether an expected tool was actually called |
| Answer quality | Rubric graded 1 to 5 by a judge model |
| Groundedness | Whether every figure stated appears in a tool result |
| Security | Whether an order was disclosed without matching contact |
| Cost and latency | Recorded per turn from the API usage figures |

The security cases are the ones worth watching. They cover a wrong phone number,
a four-digit fragment, an asserted claim of ownership, prompt injection that asks
the assistant to bypass verification, and a marketplace order code paired with a
contact that does not match it.

## Operations dashboard

`/admin` in the frontend reads aggregations over the telemetry collection: turn
and conversation volume, intent distribution, tool usage, escalation rate,
prompt cache hit rate, latency, spend per turn, and a review queue of answers
flagged as ungrounded.

The cache hit rate is the one to watch during development. If it falls to zero,
something volatile has entered the prompt prefix and caching has silently
stopped, which raises cost several-fold without any visible symptom.

The admin router requires staff sign-in (`app/api/auth.py`): one password,
`ADMIN_PASSWORD`, and a stateless 12-hour session token. Without a configured
password the staff pages stay locked (503), never open.

## Deployment

Pushing to `main` deploys: [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml)
runs the tests, builds the images on GitHub's runners, and updates the Container
App, the analytics job and the Static Web App. It signs in to Azure with OpenID
Connect through a managed identity (`infra/setup-github-oidc.sh`, run once), so no
password or key is stored in GitHub.

Manual deployment from a machine with Docker:

```bash
LOCATION=eastasia REGISTRY=<registry name> BUILD_MODE=local ./infra/deploy-backend.sh
BACKEND_URL=https://<printed url> ./infra/deploy-frontend.sh
./infra/deploy-analytics.sh        # Key Vault, managed identity, hourly analytics job
```

The backend runs on Azure Container Apps, scaled to zero so an idle demo costs
nothing, with secrets passed as Container Apps secrets rather than baked into the
image. The frontend is a static bundle on Azure Static Web Apps. It reads the
backend's URL at runtime from `config.js`, written by the deploy script, and calls
it directly; the backend admits the site through `CORS_ORIGINS`.

By default the image is built in Azure with `az acr build`. Azure for Students
subscriptions refuse that (`TasksOperationsNotAllowed`), so `BUILD_MODE=local`
builds with the local Docker instead, pinned to `linux/amd64`: an unpinned build
on Apple Silicon produces an image that fails with `exec format error`.

See [docs/setup.md](docs/setup.md) for the full walkthrough and troubleshooting.

## Documentation

| Document | Contents |
|---|---|
| [PROJECT.md](PROJECT.md) | Project state: the scenario, what is done, what is left, and the conventions |
| [docs/setup.md](docs/setup.md) | From an empty machine to a working chatbot, with troubleshooting |
| [docs/architecture.md](docs/architecture.md) | Design record: the life of a turn, retrieval design, the order-lookup threat model, evaluation methodology, and known limitations |

## Data

All data is **synthetic**. Product names, customers, and orders are generated; the policy documents are written for this project and describe a fictional store. Nothing here comes from a real e-commerce platform, and no marketplace API is called. The Shopee, Lazada, and TikTok Shop order codes are generated in the shape those platforms use, so that the consolidated-order model is exercised realistically; they correspond to no real order.
