-- Silver: typed, cleaned, and unified across channels.
--
-- The hard part lives here. Shopee, Lazada and TikTok Shop describe the same
-- facts differently: Shopee says "COMPLETED" and counts seconds since 1970,
-- Lazada says "delivered" and writes "2026-05-17 10:00:00 +0000", TikTok Shop
-- says 130 and nests money inside a payment object as strings. Silver maps all
-- of them onto one vocabulary so Gold can compare channels at all.
--
-- Dynamic tables: Snowflake refreshes each one when its inputs change, within
-- TARGET_LAG, so the medallion stays current without a hand-written scheduler.

USE ROLE NL_PIPELINE;
USE WAREHOUSE NL_WH;
USE DATABASE NORTHLIGHT_DW;

-- Latest copy of each raw record. A file loaded twice (or re-extracted) must
-- not double-count orders.
--  * Marketplace and history sources land once: drop exact duplicate records.
--  * The live app (source = app) is exported as a full snapshot on every
--    scheduled run, so only its newest snapshot counts; otherwise an order whose
--    status changed would appear once per snapshot.
CREATE OR REPLACE DYNAMIC TABLE SILVER.RAW_LATEST
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT source, dataset, extract_dt, payload, loaded_at
FROM BRONZE.RAW_EVENTS
WHERE source <> 'app'
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source, dataset, HASH(payload)
    ORDER BY loaded_at DESC) = 1
UNION ALL
SELECT source, dataset, extract_dt, payload, loaded_at
FROM BRONZE.RAW_EVENTS
WHERE source = 'app'
QUALIFY DENSE_RANK() OVER (
    PARTITION BY dataset
    ORDER BY extract_dt DESC, loaded_at DESC) = 1;

-- Orders: the seller's order system (OMS) is the backbone, one row per order
-- from any channel. The app's live orders are the same shape.
CREATE OR REPLACE DYNAMIC TABLE SILVER.ORDERS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT
    payload:order_code::STRING                         AS order_code,
    payload:channel::STRING                            AS channel,
    payload:channel_order_code::STRING                 AS channel_order_code,
    COALESCE(payload:customer_key::STRING, payload:phone_hash::STRING) AS customer_key,
    COALESCE(payload:province::STRING, payload:destination::STRING)    AS province,
    payload:status::STRING                             AS status,
    payload:carrier::STRING                            AS carrier,
    payload:subtotal::NUMBER                           AS subtotal_vnd,
    payload:shipping_fee::NUMBER                       AS shipping_fee_vnd,
    payload:total::NUMBER                              AS total_vnd,
    TRY_TO_TIMESTAMP_TZ(payload:created_at::STRING)    AS created_at,
    TRY_TO_TIMESTAMP_TZ(payload:delivered_at::STRING)  AS delivered_at,
    payload:promised_days::NUMBER                      AS promised_days,
    payload:delivery_days::NUMBER                      AS delivery_days,
    payload:delivery_days::NUMBER > payload:promised_days::NUMBER AS is_late,
    COALESCE(payload:is_simulated::BOOLEAN, FALSE)     AS is_simulated,
    source                                             AS record_source
FROM SILVER.RAW_LATEST
WHERE dataset = 'orders' AND source IN ('oms', 'app');

CREATE OR REPLACE DYNAMIC TABLE SILVER.ORDER_ITEMS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT
    o.payload:order_code::STRING      AS order_code,
    o.payload:channel::STRING         AS channel,
    i.value:sku::STRING               AS sku,
    i.value:category::STRING          AS category,
    i.value:quantity::NUMBER          AS quantity,
    i.value:unit_price::NUMBER        AS unit_price_vnd,
    i.value:list_price::NUMBER        AS list_price_vnd,
    COALESCE(i.value:discounted::BOOLEAN, FALSE) AS was_discounted
FROM SILVER.RAW_LATEST o, LATERAL FLATTEN(input => o.payload:items) i
WHERE o.dataset = 'orders' AND o.source IN ('oms', 'app');

-- Marketplace facts the OMS does not carry: the platform's own status and the
-- fees it deducts. Three vocabularies in, one out.
CREATE OR REPLACE DYNAMIC TABLE SILVER.MARKETPLACE_ORDERS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT 'shopee' AS channel,
       payload:order_sn::STRING AS channel_order_code,
       CASE payload:order_status::STRING
            WHEN 'COMPLETED' THEN 'delivered' WHEN 'CANCELLED' THEN 'cancelled'
            WHEN 'TO_RETURN' THEN 'returned' WHEN 'SHIPPED' THEN 'shipped'
            WHEN 'READY_TO_SHIP' THEN 'packing' ELSE 'pending' END AS platform_status,
       TO_TIMESTAMP_LTZ(payload:create_time::NUMBER)::TIMESTAMP_LTZ AS created_at,
       payload:total_amount::NUMBER AS gross_vnd,
       payload:escrow:commission_fee::NUMBER + payload:escrow:service_fee::NUMBER AS platform_fees_vnd
FROM SILVER.RAW_LATEST WHERE source = 'shopee' AND dataset = 'orders'
UNION ALL
SELECT 'lazada',
       payload:order_number::STRING,
       CASE payload:statuses[0]::STRING
            WHEN 'delivered' THEN 'delivered' WHEN 'canceled' THEN 'cancelled'
            WHEN 'returned' THEN 'returned' WHEN 'shipped' THEN 'shipped'
            WHEN 'ready_to_ship' THEN 'packing' WHEN 'packed' THEN 'packing' ELSE 'pending' END,
       TRY_TO_TIMESTAMP_TZ(payload:created_at::STRING, 'YYYY-MM-DD"T"HH24:MI:SS TZHTZM')::TIMESTAMP_LTZ,
       TRY_TO_NUMBER(payload:price::STRING, 18, 2)::NUMBER,
       payload:fees:commission::NUMBER + payload:fees:payment_fee::NUMBER
FROM SILVER.RAW_LATEST WHERE source = 'lazada' AND dataset = 'orders'
UNION ALL
SELECT 'tiktok_shop',
       payload:id::STRING,
       CASE payload:status::NUMBER
            WHEN 130 THEN 'delivered' WHEN 140 THEN 'cancelled' WHEN 121 THEN 'shipped'
            WHEN 122 THEN 'shipped' WHEN 112 THEN 'packing' WHEN 111 THEN 'packing' ELSE 'pending' END,
       TO_TIMESTAMP_LTZ(payload:create_time::NUMBER),
       TRY_TO_NUMBER(payload:payment:total_amount::STRING),
       payload:fees:commission::NUMBER
FROM SILVER.RAW_LATEST WHERE source = 'tiktok_shop' AND dataset = 'orders';

CREATE OR REPLACE DYNAMIC TABLE SILVER.REVIEWS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT source AS channel,
       COALESCE(payload:order_sn, payload:order_id)::STRING AS channel_order_code,
       payload:sku::STRING AS sku,
       COALESCE(payload:rating_star, payload:product_rating, payload:star)::NUMBER AS rating,
       payload:comment::STRING AS comment,
       COALESCE(TO_TIMESTAMP_LTZ(TRY_TO_NUMBER(payload:create_time::STRING)),
                TRY_TO_TIMESTAMP_TZ(payload:review_time::STRING)::TIMESTAMP_LTZ) AS reviewed_at
FROM SILVER.RAW_LATEST WHERE dataset = 'reviews';

CREATE OR REPLACE DYNAMIC TABLE SILVER.LISTING_STATS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT source AS channel, payload:sku::STRING AS sku, payload:week::STRING AS iso_week,
       payload:views::NUMBER AS views, payload:clicks::NUMBER AS clicks, payload:units::NUMBER AS units
FROM SILVER.RAW_LATEST WHERE dataset = 'listing_stats';

CREATE OR REPLACE DYNAMIC TABLE SILVER.WEB_EVENTS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT payload:session_id::STRING AS session_id, payload:customer_key::STRING AS customer_key,
       TRY_TO_TIMESTAMP_TZ(payload:at::STRING) AS at, payload:event::STRING AS event,
       payload:sku::STRING AS sku, LOWER(payload:query::STRING) AS query,
       payload:value::NUMBER AS value_vnd
FROM SILVER.RAW_LATEST WHERE source = 'website' AND dataset = 'events';

-- Chatbot turns: simulated history plus the live app's telemetry, one shape.
CREATE OR REPLACE DYNAMIC TABLE SILVER.CHAT_TURNS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT payload:session_id::STRING AS session_id,
       payload:customer_key::STRING AS customer_key,
       TRY_TO_TIMESTAMP_TZ(payload:at::STRING) AS at,
       payload:lang::STRING AS lang,
       payload:intent::STRING AS intent,
       payload:text::STRING AS question_masked,
       payload:tools_used AS tools_used,
       payload:latency_ms::NUMBER AS latency_ms,
       payload:cost_usd::FLOAT AS cost_usd,
       COALESCE(payload:grounded::BOOLEAN, TRUE) AS grounded,
       COALESCE(payload:blocked::BOOLEAN, FALSE) AS blocked,
       COALESCE(payload:escalated::BOOLEAN, FALSE) AS escalated,
       payload:topic::STRING AS policy_topic,
       payload:unmet_query::STRING AS unmet_query,
       payload:budget_vnd::NUMBER AS budget_vnd,
       payload:sku::STRING AS sku,
       payload:verified_order_code::STRING AS verified_order_code,
       COALESCE(payload:is_simulated::BOOLEAN, FALSE) AS is_simulated
FROM SILVER.RAW_LATEST WHERE dataset = 'turns';

CREATE OR REPLACE DYNAMIC TABLE SILVER.PRODUCTS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT payload:sku::STRING AS sku, payload:name_vi::STRING AS name_vi, payload:brand::STRING AS brand,
       payload:category::STRING AS category, payload:price::NUMBER AS price_vnd,
       payload:sale_price::NUMBER AS sale_price_vnd, payload:stock::NUMBER AS stock,
       payload:rating::FLOAT AS rating, payload:listed_on AS listed_on
FROM SILVER.RAW_LATEST WHERE source = 'app' AND dataset = 'products';
