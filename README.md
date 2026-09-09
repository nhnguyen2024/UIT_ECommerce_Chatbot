# E-Commerce Support Chatbot

A customer-support chatbot for an e-commerce platform, covering three jobs:

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
    db/                  Client, schema, indexes, vector search
    agent/tools/         The six tools
  seed/                  Catalogue, orders, and policy documents
  tests/
frontend/                Angular 22 workspace
infra/                   Azure deployment
docs/
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

```bash
.venv/bin/python -m pytest          # tests that need no database
.venv/bin/python -m seed.catalog    # inspect the generated catalogue
.venv/bin/python -m seed.orders     # inspect the generated orders
.venv/bin/python -m seed.parse_policies
```

## Data

All data is **synthetic**. Product names, customers, and orders are generated; the policy documents are written for this project and describe a fictional store. Nothing here comes from a real e-commerce platform.
