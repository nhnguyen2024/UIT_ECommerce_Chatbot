# Setup

From an empty machine to a working chatbot. Roughly 30 minutes, most of it
waiting for Atlas to build search indexes.

## 1. Prerequisites

| Tool | Why | Check |
|---|---|---|
| Python 3.12 or newer | Backend | `python3 --version` |
| Node 20 or newer | Angular frontend | `node --version` |
| An Anthropic API key | The agent and the classifier | [console.anthropic.com](https://console.anthropic.com) |
| A MongoDB Atlas account | Catalogue, policies, orders, vectors | [mongodb.com/atlas](https://www.mongodb.com/atlas) |

Docker is optional. It is only needed to build the deployment image.

## 2. Create the Atlas cluster

1. Create a **free M0 cluster**. Choose **Azure** as the provider and a region
   near you, such as Southeast Asia. Matching the provider to where the backend
   will run keeps latency down and avoids cross-cloud egress.
2. Under **Database Access**, create a user with a password. Note both.
3. Under **Network Access**, add your current IP address. Without this the
   connection hangs rather than failing, which is a confusing way to lose an
   afternoon.
4. Press **Connect**, choose **Drivers**, and copy the connection string.

If your password contains any of `@ : / ? # [ ] %`, percent-encode it in the
connection string or the URI will not parse.

## 3. Configure the backend

```bash
cd backend
cp .env.example .env
```

Edit `.env` and set `MONGODB_URI`. Leave `EMBEDDING_MODE=auto` so Atlas
generates the vectors. Set `ADMIN_PASSWORD` to a long random string: it is the
staff password for the Operations and Insights pages, which stay locked without it.

Then choose the model provider, with `LLM_PROVIDER`:

- **`azure_openai`** (what this project uses): a `gpt-5-mini` deployment, paid
  from Azure credit, including an Azure for Students subscription.
  1. Pick a region your subscription allows **and** that has GlobalStandard
     quota for the model. Check with
     `az cognitiveservices usage list -l <region>`. On the student subscription
     used here, that meant `japaneast` or `koreacentral`.
  2. Create the resource and the deployment:
     ```bash
     az group create -n rg-uit-chatbot -l eastasia
     az cognitiveservices account create -n <name> -g rg-uit-chatbot -l japaneast \
       --kind AIServices --sku S0 --custom-domain <name> --yes
     az cognitiveservices account deployment create -g rg-uit-chatbot -n <name> \
       --deployment-name gpt-5-mini --model-name gpt-5-mini --model-version 2025-08-07 \
       --model-format OpenAI --sku-name GlobalStandard --sku-capacity 100
     ```
     Capacity 100 is 100K tokens and 100 requests a minute.
  3. In `.env` set `AZURE_OPENAI_ENDPOINT=https://<name>.openai.azure.com`,
     `AZURE_OPENAI_API_KEY` (from `az cognitiveservices account keys list`),
     `AGENT_MODEL=gpt-5-mini`, `CLASSIFIER_MODEL=gpt-5-mini`,
     `AGENT_EFFORT=medium`, and the three `PRICE_*` values noted in `.env.example`.
- **`anthropic`**: set `ANTHROPIC_API_KEY` from
  [console.anthropic.com](https://console.anthropic.com).
- **`cortex`**: calls Claude through Snowflake Cortex, billed in Snowflake credits.
  **Not available on a Snowflake trial**, which blocks the COMPLETE function.
  1. Use a paid Snowflake account.
  2. Open a SQL worksheet in Snowsight, paste in
     [infra/snowflake/setup.sql](../infra/snowflake/setup.sql), and run all of it.
     It enables cross-region inference, creates a service user that can only
     call Cortex, and prints a token that is valid for 45 days.
  3. Copy the `TOKEN_SECRET` value into `SNOWFLAKE_PAT`. It is shown only once.
  4. Set `SNOWFLAKE_ACCOUNT_URL` to your account URL, for example
     `https://myorg-myaccount.snowflakecomputing.com`, with no path.

Check the provider before going further. This needs no database:

```bash
.venv/bin/python -m app.agent.probe
```

Every required check must pass. Copy the `LLM_STRICT_TOOLS` and `LLM_EFFORT`
values it prints into `.env`.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 4. Seed the database

```bash
.venv/bin/python -m seed.load
```

This creates the collections and indexes, generates 480 products and 325 orders,
parses the five policy documents into 26 citable chunks, and waits for the Atlas
search indexes to become queryable. The wait is normal and takes a few minutes on
the free tier: Atlas is embedding every product and policy chunk with Voyage AI.

Confirm retrieval works before involving the model:

```bash
.venv/bin/python -m seed.smoke
```

## 5. Run it

Two terminals.

```bash
# Terminal 1
cd backend && .venv/bin/python -m uvicorn app.main:app --reload

# Terminal 2
cd frontend && npm install && npm start
```

Open http://localhost:4200 for the chat and http://localhost:4200/admin for the
operations dashboard (sign in with `ADMIN_PASSWORD`). The Angular dev server proxies `/api` to port 8000, so
there is no CORS configuration to do locally.

## 6. Check it end to end

Try one of each kind of request:

| Ask | What should happen |
|---|---|
| `Chính sách đổi trả trong bao lâu?` | Answers 7 days, with a citation chip |
| `Tìm tai nghe dưới 500k` | Product cards, all at or below 500,000đ |
| `Đơn DH2026090001, sđt 0901234567` | Reports the order is out for delivery |
| `Đơn Shopee 250905K7MQ2XPL, sđt 0901234567` | The same order, found by its Shopee code |
| `Mua trên Shopee muốn trả hàng thì làm sao?` | Cites the Shopee process, 15 days, in the Shopee app |
| `Đơn DH2026090001, sđt 0900000000` | Refuses: the contact does not match |
| `Đơn Shopee 250905K7MQ2XPL, sđt 0900000000` | Refuses identically: the second code is not a way in |
| `Thủ đô nước Pháp là gì?` | Declines as out of scope |

The last two refusals are the important ones. If either reveals the order status,
identity verification is broken.

The two Shopee rows are what demonstrate the consolidated-order design: the same
order is reachable by the code the shopper actually has, and widening the lookup
key did not widen what can be read.

## 7. Measure it

```bash
cd backend
.venv/bin/python -m evals.run_eval --limit 5      # smoke test, a few cents
.venv/bin/python -m evals.run_eval                # full 56-case run
```

The runner prints a cost estimate and waits for confirmation before spending.
Results land in `evals/results/` as JSON plus a Markdown table.

## 8. Deploy

First deployment, from a machine with Docker and `az login`:

```bash
REGISTRY=<registry> BUILD_MODE=local ./infra/deploy-backend.sh
BACKEND_URL=https://<the url it printed> ./infra/deploy-frontend.sh
./infra/deploy-analytics.sh     # Key Vault, managed identity, hourly analytics job
./infra/setup-github-oidc.sh    # lets GitHub Actions deploy without stored secrets
```

Then set `CORS_ORIGINS` on the backend to the frontend URL, and widen Atlas
Network Access so Azure can reach the cluster. Both scripts print the exact
commands when they finish. Add the three values `setup-github-oidc.sh` prints as
repository variables in GitHub (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`).

After that, every push to `main` deploys through `.github/workflows/ci-cd.yml`:
tests, then images built on GitHub's runners, then the Container App, the
analytics job and the Static Web App updated.

## Troubleshooting

**The seed script hangs.** Almost always Atlas Network Access. Add your IP.

**Search returns nothing after seeding.** The index is still building. Atlas
returns an empty result rather than an error while an index is not yet
queryable. Check the Atlas UI, or re-run `seed.smoke` in a minute.

**`cache_read_input_tokens` is always zero.** Something volatile has entered the
prompt prefix, which invalidates the cache for every conversation. The system
prompt and tool definitions must be byte-identical between requests.

**The browser shows a CORS error in production.** `CORS_ORIGINS` on the backend
does not include the frontend origin. It is a JSON array, and the scheme must
match exactly.

**`exec format error` on the Container App.** The image was built for arm64 on an
Apple Silicon machine. Build with `--platform linux/amd64` (what `BUILD_MODE=local`
and the deploy scripts do), or let GitHub Actions build it; a plain local
`docker build && docker push` on a Mac produces the wrong architecture.
