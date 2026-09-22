"""Run the analytics loop end to end.

    analytics/.venv/bin/python analytics/pipeline.py all        # every step below
    analytics/.venv/bin/python analytics/pipeline.py extract    # app data -> analytics/out/raw
    analytics/.venv/bin/python analytics/pipeline.py upload     # analytics/out/raw -> Azure Data Lake
    analytics/.venv/bin/python analytics/pipeline.py snowflake  # bronze -> silver -> gold
    analytics/.venv/bin/python analytics/pipeline.py feedback   # gold -> app (insights)

The loop:

    app (MongoDB) + marketplace extracts ──extract──> Azure Data Lake (raw JSON, by date)
        ──COPY──> Snowflake BRONZE ──> SILVER ──> GOLD
        ──feedback──> MongoDB `insights` ──> Operations dashboard, and new eval cases

Secrets come from analytics/.env (the lake) and backend/.env (MongoDB). The
Snowflake token and the SAS token are never printed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
OUT = ROOT / "out" / "raw"
SQL = ROOT / "snowflake"


def load_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip("'\"")
    return values


ENV = {**load_env(REPO / "backend" / ".env"), **load_env(ROOT / ".env")}


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
    today = dt.date.today().isoformat()

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
    subprocess.run(
        ["az", "storage", "fs", "directory", "upload", "-f", ENV["ADLS_CONTAINER"],
         "--account-name", ENV["ADLS_ACCOUNT"], "--account-key", ENV["ADLS_KEY"],
         "-s", f"{OUT}/*", "-d", "raw", "--recursive", "-o", "none"],
        check=True,
    )
    print("  uploaded analytics/out/raw -> lake/raw")


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
                       .replace("{{ADLS_SAS}}", ENV["ADLS_SAS"].lstrip("?"))
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    for chunk in re.split(r";\s*\n", "\n".join(lines) + "\n"):
        if chunk.strip():
            yield chunk.strip()


def snowflake_run() -> None:
    conn = connect()
    cur = conn.cursor()
    for script in ("01_bronze.sql", "02_silver.sql", "03_gold.sql"):
        for stmt in statements((SQL / script).read_text()):
            head = " ".join(stmt.split())[:70]
            cur.execute(stmt)
            print(f"  {script}: {head}")
    # Dynamic tables refresh on their lag; force one refresh now so results are immediate.
    cur.execute("SHOW DYNAMIC TABLES IN DATABASE NORTHLIGHT_DW")
    names = [(row[2], row[1]) for row in cur.fetchall()]  # (schema, name)
    order = ["RAW_LATEST", "ORDERS", "ORDER_ITEMS", "MARKETPLACE_ORDERS", "REVIEWS", "LISTING_STATS",
             "WEB_EVENTS", "CHAT_TURNS", "PRODUCTS", "CUSTOMER_RFM", "REPEAT_DRIVERS", "CHANNEL_PERFORMANCE",
             "CHANNEL_MIGRATION", "DEMAND_GAPS", "PRICE_GAPS", "DELIVERY_PERFORMANCE", "REVIEW_THEMES",
             "CHATBOT_QUALITY", "WEB_FUNNEL", "WINBACK_CANDIDATES", "INSIGHT_ACTIONS"]
    for name in order:
        for schema, table in names:
            if table == name:
                cur.execute(f"ALTER DYNAMIC TABLE {schema}.{table} REFRESH")
    cur.execute("ALTER TASK BRONZE.LOAD_FROM_LAKE RESUME")
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
    print("  wrote insights document")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"extract": [extract], "upload": [upload], "snowflake": [snowflake_run], "feedback": [feedback],
             "all": [extract, upload, snowflake_run, feedback]}[step]
    for fn in steps:
        print(f"==> {fn.__name__}")
        fn()
