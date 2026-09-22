# Analytics pipeline: closing the loop

The chatbot answers customers. Every conversation is also a record of what
customers want, where they get stuck, and why they do or don't come back. This
pipeline turns that record, together with orders and marketplace data, into
actions, and hands those actions back to the app.

```
Chatbot + shop (MongoDB) ─┐
Shopee / Lazada / TikTok ─┼─ extract ─▶ Azure Data Lake Gen2   raw/source=<s>/<dataset>/dt=<date>/*.json
Website clickstream ──────┘                     │ external stage (SAS, read + list)
                                                ▼
                       Snowflake NORTHLIGHT_DW:  BRONZE ─▶ SILVER ─▶ GOLD
                                                                      │ feedback
                                                                      ▼
                    MongoDB `insights` ─▶ /admin/insights in the app (actions for each team)
                                       └▶ the chatbot's own backlog: new policy content, new eval cases
```

## Layers

| Layer | What it holds | Why |
|---|---|---|
| **Lake** | JSON exactly as extracted, partitioned by source, dataset and date | Cheap, replayable; nothing is lost if a later step is wrong |
| **Bronze** (`BRONZE.RAW_EVENTS`) | Every record as a VARIANT, with its file name and load time | Loaded incrementally by `COPY INTO` (which skips files it already loaded), run by the hourly Azure job |
| **Silver** | Typed, deduplicated, unified tables: orders, items, marketplace orders, reviews, listing stats, web events, chat turns, products | The three marketplaces use different field names, status codes and time formats; Silver maps them to one vocabulary |
| **Gold** | One mart per business question, plus `INSIGHT_ACTIONS` | What the business reads, and what goes back into the app |

Silver and gold are **dynamic tables**: Snowflake refreshes them when their
inputs change, within the target lag, so there is no scheduler to maintain.

## Gold marts

| Mart | Question |
|---|---|
| `CUSTOMER_RFM` | Who are our customers: champions, loyal, at risk, one-time, lost? |
| `REPEAT_DRIVERS` | What makes a second purchase more or less likely (first delivery late, returned, used the chatbot, first channel)? |
| `CHANNEL_PERFORMANCE` | Shopee vs Lazada vs TikTok Shop vs website: GMV, AOV, cancellations, returns, late deliveries, platform fees, ratings, repeat rate |
| `CHANNEL_MIGRATION` | Where do repeat customers buy their next order? |
| `DEMAND_GAPS` | What do shoppers ask the chatbot for that we don't stock, or that is out of stock? |
| `PRICE_GAPS` | Which budgets do shoppers state, and how many in-stock products fit them? |
| `DELIVERY_PERFORMANCE` | Which carrier and region are late, and does lateness bring more "where is my order" chats and lower ratings? |
| `REVIEW_THEMES` | What do marketplace reviews complain about? |
| `CHATBOT_QUALITY` | Which intents and policy topics escalate or go ungrounded; latency and cost |
| `WEB_FUNNEL` | Website sessions from product view to purchase |
| `WINBACK_CANDIDATES` | One-time buyers worth a campaign (hashed keys only) |
| `INSIGHT_ACTIONS` | Each finding as an action, with its evidence and the team that owns it |

## Data: real and simulated

- **Real:** the live app's products, orders, chatbot telemetry and handoffs, exported by `pipeline.py extract`. Contacts stay hashed; phone numbers and emails in chat text are masked before export.
- **Simulated:** twelve months of history (`simulate.py`), because the live system is weeks old and no marketplace API is connected. Every simulated record carries `is_simulated: true`. Marketplace extracts use each platform's own field names and status codes.
- The simulation plants a few relationships on purpose (late deliveries lower ratings and repeat purchases; some asked-for products aren't stocked; one carrier is slow in central provinces; instalment questions have no policy). The gold layer's job is to recover them. **These are demonstrations of the pipeline, not findings about a real business.**

## Running it

In production the loop runs **hourly in Azure** as a Container Apps Job
(`infra/deploy-analytics.sh`): `pipeline.py scheduled` extracts from the app,
uploads to the lake with the job's managed identity, redeploys the SQL if the
scripts changed, loads new files into bronze, refreshes silver and gold, and
writes gold back to MongoDB. Secrets come from Key Vault. Run it now with
`./infra/deploy-analytics.sh run`.

Locally, for development:

```bash
python3 -m venv analytics/.venv && analytics/.venv/bin/pip install -r analytics/requirements.txt
backend/.venv/bin/python analytics/simulate.py          # synthetic history -> analytics/out/raw
analytics/.venv/bin/python analytics/pipeline.py all    # extract, upload, snowflake, feedback
```

One-time Snowflake setup: run `snowflake/00_admin_setup.sql` as ACCOUNTADMIN in a
Snowsight worksheet, and put the token it prints into `analytics/.env` as
`SNOWFLAKE_PAT='...'`.

`analytics/.env` (git-ignored) holds the lake's account key, a read-only SAS
token for Snowflake, and the Snowflake token. None of them is ever printed.

## Why these choices

| Choice | Instead of | Why |
|---|---|---|
| Azure Data Lake Gen2 as landing zone | Loading MongoDB straight into Snowflake | Decouples sources from the warehouse; files are replayable; the same lake can feed other tools |
| SAS-token external stage | Storage integration | An integration needs a tenant admin to consent to Snowflake's Azure app, which a university tenant doesn't grant students |
| Dynamic tables | Streams + tasks per table | Declarative: each table states its query and freshness, and Snowflake works out the refresh order |
| Rule-based review themes | Cortex sentiment/classification | Works on a trial account and is explainable in a defense; Cortex can replace the rules later |
| Results written back to MongoDB | Asking business users to open Snowflake | The loop closes inside the app the team already uses |
