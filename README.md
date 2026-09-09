# Northlight Support Chatbot

A customer-support chatbot for **Northlight**, a fictional Vietnamese consumer
electronics retailer. The catalogue covers phones, tablets, laptops, audio,
wearables, televisions, home appliances, and charging accessories, in the mould
of Thế Giới Di Động or FPT Shop.

Electronics rather than general merchandise on purpose: warranty terms, return
windows, and spec comparison all carry real weight on expensive goods, so all
three jobs below have something substantial to work with.

It covers three jobs:

1. **Product consultation** — semantic and keyword search over a product catalogue, with filters on category, price, and rating.
2. **Policy questions** — retrieval-augmented answers over store policy documents, where every claim cites the passage it came from.
3. **Order tracking** — status and delivery timeline lookups, gated behind identity verification.

The chatbot answers in **Vietnamese and English**. All source code, comments, and documentation are in English; both languages appear only as content.

## Architecture

```
Angular 22 SPA  (Azure Static Web Apps)
      |  HTTPS, Server-Sent Events
      v
FastAPI  (Azure Container Apps)
      |- Turn classifier      claude-haiku-4-5
      |- Agent orchestrator   claude-opus-5, hand-written tool loop
      |- Six tools over MongoDB
      |- Guardrails and groundedness checks
      '- Telemetry
      |
      v
MongoDB Atlas M0  (Automated Embedding, Voyage AI)
```

## Design decisions worth knowing

**The agent loop is hand-written, not delegated to the SDK tool runner.** The runner keeps its own message history and does not expose it, so an app that persists conversations has to mirror history anyway. A manual loop also lets the backend emit progress events mid-turn and run a guardrail check between rounds.

**Order codes alone never unlock an order.** Codes are short and get shared in screenshots. `get_order_status` requires a matching phone number or email, compares salted hashes, and returns an identical response whether the code is wrong or the contact is wrong, so it cannot be used to test which codes exist. The database stores no readable contact details.

**Policy answers are never generated from model memory.** Return windows and fee thresholds are business facts that change. The model must retrieve a passage and cite its `chunk_id`.

**Policy chunks are split by hand-authored section, not by token count.** A token-based chunker renumbers chunks whenever text is edited, which would rot every citation in the evaluation dataset.

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
infra/                   Azure deployment scripts, docker compose
docs/                    Setup guide
```

## Setup

Requires Python 3.12 or later and a MongoDB Atlas cluster.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env      # then fill in ANTHROPIC_API_KEY and MONGODB_URI
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

A 47-case bilingual dataset covering policy questions, product consultation,
order tracking, and adversarial input.

```bash
cd backend
.venv/bin/python -m evals.run_eval --limit 5   # smoke test, a few cents
.venv/bin/python -m evals.run_eval             # full run
.venv/bin/python -m evals.run_eval --no-judge  # labels only, nearly free
```

Every run costs money, so the runner prints an estimate and waits for
confirmation. Results are written to `evals/results/` as JSON and as a Markdown
table ready to paste into a report.

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
a four-digit fragment, an asserted claim of ownership, and prompt injection that
asks the assistant to bypass verification.

## Operations dashboard

`/admin` in the frontend reads aggregations over the telemetry collection: turn
and conversation volume, intent distribution, tool usage, escalation rate,
prompt cache hit rate, latency, spend per turn, and a review queue of answers
flagged as ungrounded.

The cache hit rate is the one to watch during development. If it falls to zero,
something volatile has entered the prompt prefix and caching has silently
stopped, which raises cost several-fold without any visible symptom.

The admin router has **no authentication** in this build. Put a gate in front of
it before exposing it anywhere public: the summary reveals operating cost and
the review queue quotes shopper messages.

## Deployment

```bash
./infra/deploy-backend.sh
BACKEND_URL=https://<printed url> ./infra/deploy-frontend.sh
```

The backend runs on Azure Container Apps, scaled to zero so an idle demo costs
nothing, with secrets passed as Container Apps secrets rather than baked into the
image. The frontend is a static bundle on Azure Static Web Apps, which rewrites
`/api/*` to the backend so no hostname is compiled into the client.

The image is built with `az acr build` rather than locally, which produces a
linux/amd64 image regardless of the developer's machine. Building locally on
Apple Silicon and pushing produces an image that fails with `exec format error`.

See [docs/setup.md](docs/setup.md) for the full walkthrough and troubleshooting.

## Data

All data is **synthetic**. Product names, customers, and orders are generated; the policy documents are written for this project and describe a fictional store. Nothing here comes from a real e-commerce platform.
