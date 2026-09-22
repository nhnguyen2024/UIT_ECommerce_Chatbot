"""Run the analytics loop end to end.

    analytics/.venv/bin/python analytics/pipeline.py all        # every step below
    analytics/.venv/bin/python analytics/pipeline.py extract    # app data -> analytics/out/raw
    analytics/.venv/bin/python analytics/pipeline.py upload     # analytics/out/raw -> Azure Data Lake
    analytics/.venv/bin/python analytics/pipeline.py snowflake  # (re)deploy bronze/silver/gold SQL, refresh
    analytics/.venv/bin/python analytics/pipeline.py load       # new lake files -> bronze (COPY)
    analytics/.venv/bin/python analytics/pipeline.py refresh    # refresh silver and gold now
    analytics/.venv/bin/python analytics/pipeline.py feedback   # gold -> app (insights)
    analytics/.venv/bin/python analytics/pipeline.py scheduled  # extract, upload, ensure_sql, load, refresh, feedback

In production "scheduled" runs hourly as an Azure Container Apps Job
(infra/deploy-analytics.sh). Nothing in the loop depends on a developer machine.

The loop:

    app (MongoDB) + marketplace extracts ──extract──> Azure Data Lake (raw JSON, by date)
        ──COPY──> Snowflake BRONZE ──> SILVER ──> GOLD
        ──feedback──> MongoDB `insights` ──> Operations dashboard, and new eval cases

Settings come from environment variables. In Azure the job receives them from
Key Vault through its managed identity, and reaches the lake with that identity
(no storage key). Locally they are read from analytics/.env and backend/.env.
The Snowflake token and the SAS token are never printed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
OUT = pathlib.Path(os.environ.get("ANALYTICS_OUT", ROOT / "out")) / "raw"
SQL = ROOT / "snowflake"


def load_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip("'\"")
    return values


ENV = {**load_env(REPO / "backend" / ".env"), **load_env(ROOT / ".env"), **os.environ}


# --- extract: the live app's data --------------------------------------------------------
def mask(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", text or "")
    return re.sub(r"(\+?84|0)[\d\s.-]{8,12}\d", "<phone>", text)


def extract() -> None:
    """Export the app's collections. Contacts stay hashed; chat text is masked."""
    import certifi
    from pymongo import MongoClient

    uri = ENV["MONGODB_URI"]
    db = MongoClient(uri, tlsCAFile=certifi.where() if uri.startswith("mongodb+srv") else None)[
        ENV.get("MONGODB_DB", "uit_ecommerce_chatbot")]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()

    def write(dataset: str, rows) -> None:
        path = OUT / "source=app" / dataset / f"dt={today}"
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "part-000.json", "w", encoding="utf-8") as fh:
            count = 0
            for row in rows:
                row.pop("_id", None)
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                count += 1
        print(f"  app/{dataset:<14} {count:>6}")

    write("products", db.products.find({}, {"embedding": 0, "embedding_source": 0}))

    def orders():
        for o in db.orders.find({}, {"email_hash": 0, "customer_name": 0}):
            o["customer_key"] = o.pop("phone_hash", None)
            items = []
            for item in o.get("items", []):
                sku = item.get("sku", "")
                items.append({**item, "category": None, "list_price": item.get("unit_price")})
            o["items"] = items
            o["province"] = o.get("destination")
            yield o

    write("orders", orders())

    def turns():
        # Telemetry has no text; the question comes from the stored conversation.
        questions = {}
        for conv in db.conversations.find({}, {"session_id": 1, "messages": 1}):
            user_msgs = [m for m in conv.get("messages", []) if m.get("role") == "user"]
            questions[conv["session_id"]] = [mask(m.get("text", "")) for m in user_msgs]
        seen = {}
        for event in db.events.find({}).sort("at", 1):
            sid = event.get("session_id")
            i = seen.get(sid, 0)
            seen[sid] = i + 1
            texts = questions.get(sid, [])
            event["text"] = texts[i] if i < len(texts) else None
            yield event

    write("turns", turns())
    write("handoffs", ({**h, "summary": mask(h.get("summary", ""))} for h in db.handoffs.find({})))


# --- upload: to the lake -------------------------------------------------------------------
def upload() -> None:
    """Copy every file under out/raw to lake/raw, keeping the source=/dataset/dt= layout.

    With ADLS_KEY set (a developer machine) the account key is used. Without it
    (the Azure job) the managed identity is used, which holds only the
    "Storage Blob Data Contributor" role on this account.
    """
    from azure.storage.blob import BlobServiceClient

    if ENV.get("ADLS_KEY"):
        credential = ENV["ADLS_KEY"]
    else:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
    service = BlobServiceClient(f"https://{ENV['ADLS_ACCOUNT']}.blob.core.windows.net", credential=credential)
    container = service.get_container_client(ENV["ADLS_CONTAINER"])
    count = 0
    for path in sorted(OUT.rglob("*.json")):
        with open(path, "rb") as fh:
            container.upload_blob(f"raw/{path.relative_to(OUT).as_posix()}", fh, overwrite=True)
        count += 1
    print(f"  uploaded {count} files -> {ENV['ADLS_CONTAINER']}/raw")


# --- snowflake: bronze, silver, gold ---------------------------------------------------------
def connect():
    import snowflake.connector

    account = re.sub(r"^https?://", "", ENV["SNOWFLAKE_ACCOUNT_URL"]).split(".snowflakecomputing.com")[0]
    return snowflake.connector.connect(
        account=account,
        user=ENV.get("SNOWFLAKE_USER", "CHATBOT_SVC"),
        authenticator="PROGRAMMATIC_ACCESS_TOKEN",
        token=ENV["SNOWFLAKE_PAT"],
        role=ENV.get("SNOWFLAKE_ROLE", "NL_PIPELINE"),
        warehouse=ENV.get("SNOWFLAKE_WAREHOUSE", "NL_WH"),
        database="NORTHLIGHT_DW",
    )


def statements(sql_text: str):
    """Split a script on semicolons at line ends; comments and blanks dropped."""
    sql_text = sql_text.replace("{{ADLS_ACCOUNT}}", ENV["ADLS_ACCOUNT"]) \
                       .replace("{{ADLS_CONTAINER}}", ENV["ADLS_CONTAINER"]) \
                       .replace("{{ADLS_SAS}}", ENV.get("ADLS_SAS", "").lstrip("?"))
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    for chunk in re.split(r";\s*\n", "\n".join(lines) + "\n"):
        if chunk.strip():
            yield chunk.strip()


def snowflake_run() -> None:
    """Deploy the bronze/silver/gold definitions, then refresh. Needed only when the SQL changes."""
    conn = connect()
    cur = conn.cursor()
    for script in ("01_bronze.sql", "02_silver.sql", "03_gold.sql"):
        for stmt in statements((SQL / script).read_text()):
            head = " ".join(stmt.split())[:70]
            cur.execute(stmt)
            print(f"  {script}: {head}")
    conn.close()
    refresh()


def sql_version() -> str:
    import hashlib
    digest = hashlib.sha256()
    for script in ("01_bronze.sql", "02_silver.sql", "03_gold.sql"):
        digest.update((SQL / script).read_bytes())
    return digest.hexdigest()[:16]


def ensure_sql() -> None:
    """Redeploy the SQL only when the files in this image differ from what Snowflake runs.

    The deployed version is a hash of the three scripts, kept in
    BRONZE.PIPELINE_META. A SQL change therefore goes live on the first
    scheduled run after CI/CD ships the new image, with no manual step, and an
    unchanged image never rebuilds the dynamic tables.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS BRONZE.PIPELINE_META (key STRING, value STRING, updated_at TIMESTAMP_LTZ)")
    cur.execute("SELECT value FROM BRONZE.PIPELINE_META WHERE key = 'sql_version'")
    row = cur.fetchone()
    conn.close()
    version = sql_version()
    if row and row[0] == version:
        print(f"  SQL up to date ({version})")
        return
    print(f"  SQL changed ({row[0] if row else 'none'} -> {version}); redeploying")
    snowflake_run()
    conn = connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM BRONZE.PIPELINE_META WHERE key = 'sql_version'")
    cur.execute("INSERT INTO BRONZE.PIPELINE_META VALUES ('sql_version', %s, CURRENT_TIMESTAMP())", (version,))
    conn.close()


def load() -> None:
    """Load new lake files into bronze. COPY skips files it has already loaded."""
    copy = next(stmt for stmt in statements((SQL / "01_bronze.sql").read_text()) if stmt.startswith("COPY INTO"))
    conn = connect()
    cur = conn.cursor()
    cur.execute(copy)
    loaded = [row for row in cur.fetchall() if len(row) > 1 and row[1] == "LOADED"]
    print(f"  bronze: {len(loaded)} new files loaded")
    conn.close()


def refresh() -> None:
    """Refresh silver and gold in dependency order, so results are current when feedback runs."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SHOW DYNAMIC TABLES IN DATABASE NORTHLIGHT_DW")
    columns = [c[0].lower() for c in cur.description]
    names = [(row[columns.index("schema_name")], row[columns.index("name")]) for row in cur.fetchall()]
    order = ["RAW_LATEST", "ORDERS", "ORDER_ITEMS", "MARKETPLACE_ORDERS", "REVIEWS", "LISTING_STATS",
             "WEB_EVENTS", "CHAT_TURNS", "PRODUCTS", "CUSTOMER_RFM", "REPEAT_DRIVERS", "CHANNEL_PERFORMANCE",
             "CHANNEL_MIGRATION", "DEMAND_GAPS", "PRICE_GAPS", "DELIVERY_PERFORMANCE", "REVIEW_THEMES",
             "CHATBOT_QUALITY", "WEB_FUNNEL", "WINBACK_CANDIDATES", "INSIGHT_ACTIONS"]
    for name in order:
        for schema, table in names:
            if table == name:
                cur.execute(f"ALTER DYNAMIC TABLE {schema}.{table} REFRESH")
    for table in ("BRONZE.RAW_EVENTS", "SILVER.ORDERS", "SILVER.CHAT_TURNS", "GOLD.CUSTOMER_RFM",
                  "GOLD.INSIGHT_ACTIONS"):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table:<24} {cur.fetchone()[0]:>8,}")
    conn.close()


# --- feedback: gold back into the app -------------------------------------------------------------
GOLD_EXPORTS = {
    "actions": "SELECT area, owner, priority, finding, evidence, action FROM GOLD.INSIGHT_ACTIONS ORDER BY priority, area",
    "channels": "SELECT channel, orders, gmv_vnd, aov_vnd, cancel_rate, return_rate, late_rate, fee_share, "
                "net_revenue_vnd, avg_rating, repeat_rate FROM GOLD.CHANNEL_PERFORMANCE ORDER BY gmv_vnd DESC",
    "segments": "SELECT segment, COUNT(*) AS customers, SUM(revenue_vnd) AS revenue_vnd FROM GOLD.CUSTOMER_RFM "
                "GROUP BY 1 ORDER BY 2 DESC",
    "repeat_drivers": "SELECT driver, bucket, customers, repeat_rate FROM GOLD.REPEAT_DRIVERS ORDER BY driver, bucket",
    "demand_gaps": "SELECT gap_type, item, asks, customers FROM GOLD.DEMAND_GAPS ORDER BY asks DESC LIMIT 10",
    "delivery": "SELECT carrier, region, delivered_orders, avg_days, late_rate, tracking_chats_per_order, avg_rating "
                "FROM GOLD.DELIVERY_PERFORMANCE ORDER BY late_rate DESC",
    "chatbot": "SELECT intent, policy_topic, turns, escalation_rate, ungrounded_rate, p50_latency_ms FROM "
               "GOLD.CHATBOT_QUALITY ORDER BY turns DESC",
    "review_themes": "SELECT theme, SUM(reviews) AS reviews, AVG(avg_rating) AS avg_rating FROM GOLD.REVIEW_THEMES "
                     "GROUP BY 1 ORDER BY 2 DESC",
    "migration": "SELECT from_channel, to_channel, transitions FROM GOLD.CHANNEL_MIGRATION",
}


def feedback() -> None:
    """Copy gold results into MongoDB `insights`, where the dashboard reads them."""
    import certifi
    from pymongo import MongoClient

    conn = connect()
    cur = conn.cursor()
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc), "source": "snowflake NORTHLIGHT_DW.GOLD",
           "includes_simulated_history": True}
    for key, query in GOLD_EXPORTS.items():
        cur.execute(query)
        cols = [c[0].lower() for c in cur.description]
        doc[key] = [{c: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
                     for c, v in zip(cols, row)} for row in cur.fetchall()]
        print(f"  gold -> insights.{key:<15} {len(doc[key]):>4} rows")
    conn.close()
    uri = ENV["MONGODB_URI"]
    db = MongoClient(uri, tlsCAFile=certifi.where() if uri.startswith("mongodb+srv") else None)[
        ENV.get("MONGODB_DB", "uit_ecommerce_chatbot")]
    db.insights.insert_one(doc)
    # One document per run; a week of hourly runs is enough history.
    removed = db.insights.delete_many({"generated_at": {"$lt": doc["generated_at"] - dt.timedelta(days=7)}})
    print(f"  wrote insights document ({removed.deleted_count} older than 7 days removed)")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"extract": [extract], "upload": [upload], "snowflake": [snowflake_run], "load": [load],
             "refresh": [refresh], "feedback": [feedback],
             "scheduled": [extract, upload, ensure_sql, load, refresh, feedback],
             "all": [extract, upload, snowflake_run, feedback]}[step]
    for fn in steps:
        print(f"==> {fn.__name__}")
        fn()
