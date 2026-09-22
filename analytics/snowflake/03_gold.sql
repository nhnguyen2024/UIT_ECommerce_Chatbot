-- Gold: one mart per business question, and one table of recommended actions
-- that flows back into the app. That last table is what closes the loop.
--
-- "As of" is the latest order date in the data rather than today, so results
-- are reproducible and the simulated history reads sensibly.

USE ROLE NL_PIPELINE;
USE WAREHOUSE NL_WH;
USE DATABASE NORTHLIGHT_DW;

-- 1. Customers: RFM segments and what their first experience was ---------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.CUSTOMER_RFM
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH asof AS (SELECT MAX(created_at) AS d FROM SILVER.ORDERS),
ranked AS (
    SELECT o.*, ROW_NUMBER() OVER (PARTITION BY customer_key ORDER BY created_at) AS n
    FROM SILVER.ORDERS o WHERE customer_key IS NOT NULL
),
agg AS (
    SELECT customer_key,
           MIN(created_at) AS first_order_at,
           MAX(created_at) AS last_order_at,
           COUNT_IF(status <> 'cancelled') AS orders,
           SUM(IFF(status = 'delivered', total_vnd, 0)) AS revenue_vnd,
           MAX(IFF(n = 1, channel, NULL)) AS first_channel,
           MAX(IFF(n = 1, is_late, NULL)) AS first_order_late,
           MAX(IFF(n = 1, status = 'returned', NULL)) AS first_order_returned,
           COUNT(DISTINCT channel) AS channels_used,
           ANY_VALUE(province) AS province
    FROM ranked GROUP BY customer_key
)
SELECT a.*,
       DATEDIFF('day', a.last_order_at, asof.d) AS recency_days,
       a.orders > 1 AS is_repeat,
       CASE
           WHEN a.orders >= 4 AND DATEDIFF('day', a.last_order_at, asof.d) <= 90 THEN 'Champions'
           WHEN a.orders >= 2 AND DATEDIFF('day', a.last_order_at, asof.d) <= 120 THEN 'Loyal'
           WHEN a.orders >= 2 THEN 'At risk'
           WHEN DATEDIFF('day', a.last_order_at, asof.d) <= 60 THEN 'New'
           WHEN DATEDIFF('day', a.last_order_at, asof.d) <= 180 THEN 'One-time, winnable'
           ELSE 'Lost'
       END AS segment
FROM agg a, asof;

-- 2. What drives a second purchase ---------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.REPEAT_DRIVERS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH chat_users AS (SELECT DISTINCT customer_key FROM SILVER.CHAT_TURNS WHERE customer_key IS NOT NULL)
SELECT 'first_order_delivery' AS driver,
       IFF(first_order_late, 'late', 'on time') AS bucket,
       COUNT(*) AS customers, AVG(IFF(is_repeat, 1, 0)) AS repeat_rate
FROM GOLD.CUSTOMER_RFM WHERE first_order_late IS NOT NULL GROUP BY 1, 2
UNION ALL
SELECT 'first_order_returned', IFF(first_order_returned, 'returned', 'kept'), COUNT(*), AVG(IFF(is_repeat, 1, 0))
FROM GOLD.CUSTOMER_RFM WHERE first_order_returned IS NOT NULL GROUP BY 1, 2
UNION ALL
SELECT 'used_chatbot', IFF(c.customer_key IS NULL, 'no', 'yes'), COUNT(*), AVG(IFF(r.is_repeat, 1, 0))
FROM GOLD.CUSTOMER_RFM r LEFT JOIN chat_users c USING (customer_key) GROUP BY 1, 2
UNION ALL
SELECT 'first_channel', first_channel, COUNT(*), AVG(IFF(is_repeat, 1, 0))
FROM GOLD.CUSTOMER_RFM GROUP BY 1, 2;

-- 3. Marketplaces compared --------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.CHANNEL_PERFORMANCE
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH o AS (
    SELECT channel,
           COUNT(*) AS orders,
           SUM(IFF(status = 'delivered', total_vnd, 0)) AS gmv_vnd,
           AVG(IFF(status = 'delivered', total_vnd, NULL)) AS aov_vnd,
           AVG(IFF(status = 'cancelled', 1, 0)) AS cancel_rate,
           AVG(IFF(status = 'returned', 1, 0)) AS return_rate,
           AVG(IFF(is_late, 1, 0)) AS late_rate
    FROM SILVER.ORDERS GROUP BY channel
),
fees AS (SELECT channel, SUM(platform_fees_vnd) AS fees_vnd FROM SILVER.MARKETPLACE_ORDERS
         WHERE platform_status = 'delivered' GROUP BY channel),
ratings AS (SELECT channel, AVG(rating) AS avg_rating, COUNT(*) AS reviews FROM SILVER.REVIEWS GROUP BY channel),
repeat AS (SELECT first_channel AS channel, AVG(IFF(is_repeat, 1, 0)) AS repeat_rate
           FROM GOLD.CUSTOMER_RFM GROUP BY 1)
SELECT o.*, COALESCE(f.fees_vnd, 0) AS platform_fees_vnd,
       COALESCE(f.fees_vnd, 0) / NULLIF(o.gmv_vnd, 0) AS fee_share,
       o.gmv_vnd - COALESCE(f.fees_vnd, 0) AS net_revenue_vnd,
       r.avg_rating, r.reviews, rp.repeat_rate
FROM o LEFT JOIN fees f USING (channel) LEFT JOIN ratings r USING (channel) LEFT JOIN repeat rp USING (channel);

-- Where repeat customers buy next: marketplace first order -> which channel after?
CREATE OR REPLACE DYNAMIC TABLE GOLD.CHANNEL_MIGRATION
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH seq AS (
    SELECT customer_key, channel,
           LEAD(channel) OVER (PARTITION BY customer_key ORDER BY created_at) AS next_channel
    FROM SILVER.ORDERS WHERE status <> 'cancelled' AND customer_key IS NOT NULL
)
SELECT channel AS from_channel, next_channel AS to_channel, COUNT(*) AS transitions
FROM seq WHERE next_channel IS NOT NULL GROUP BY 1, 2;

-- 4. Demand the catalogue does not meet --------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.DEMAND_GAPS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT 'not_stocked' AS gap_type, unmet_query AS item, COUNT(*) AS asks,
       COUNT(DISTINCT customer_key) AS customers
FROM SILVER.CHAT_TURNS WHERE unmet_query IS NOT NULL GROUP BY 1, 2
UNION ALL
SELECT 'out_of_stock', t.sku || ' ' || COALESCE(p.name_vi, ''), COUNT(*), COUNT(DISTINCT t.customer_key)
FROM SILVER.CHAT_TURNS t LEFT JOIN SILVER.PRODUCTS p USING (sku)
WHERE t.intent = 'product_consultation' AND t.sku IS NOT NULL AND p.stock = 0
GROUP BY 1, 2;

-- Budgets shoppers state vs. what the catalogue offers under that budget.
CREATE OR REPLACE DYNAMIC TABLE GOLD.PRICE_GAPS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH asks AS (
    SELECT p.category, t.budget_vnd, COUNT(*) AS asks
    FROM SILVER.CHAT_TURNS t JOIN SILVER.PRODUCTS p USING (sku)
    WHERE t.budget_vnd IS NOT NULL GROUP BY 1, 2
),
supply AS (
    SELECT a.category, a.budget_vnd,
           COUNT_IF(COALESCE(p.sale_price_vnd, p.price_vnd) <= a.budget_vnd AND p.stock > 0) AS products_in_budget
    FROM asks a JOIN SILVER.PRODUCTS p ON p.category = a.category GROUP BY 1, 2
)
SELECT a.category, a.budget_vnd, a.asks, s.products_in_budget,
       a.asks / GREATEST(s.products_in_budget, 1) AS asks_per_product
FROM asks a JOIN supply s USING (category, budget_vnd);

-- 5. Delivery experience ---------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.DELIVERY_PERFORMANCE
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH region AS (
    SELECT column1 AS province, column2 AS region FROM VALUES
    ('hanoi','north'),('haiphong','north'),('quangninh','north'),('bacninh','north'),('thainguyen','north'),
    ('laocai','north'),('ninhbinh','north'),('thanhhoa','north'),('nghean','north'),('hue','central'),
    ('danang','central'),('quangngai','central'),('khanhhoa','south'),('daklak','south'),('lamdong','south'),
    ('hcm','south'),('dongnai','south'),('cantho','south'),('vinhlong','south'),('camau','south')
),
tracking AS (SELECT verified_order_code AS order_code, COUNT(*) AS tracking_chats
             FROM SILVER.CHAT_TURNS WHERE verified_order_code IS NOT NULL GROUP BY 1),
rated AS (SELECT o.order_code, r.rating FROM SILVER.REVIEWS r
          JOIN SILVER.ORDERS o ON o.channel_order_code = r.channel_order_code)
SELECT o.carrier, g.region,
       COUNT(*) AS delivered_orders,
       AVG(o.delivery_days) AS avg_days,
       AVG(IFF(o.is_late, 1, 0)) AS late_rate,
       AVG(COALESCE(t.tracking_chats, 0)) AS tracking_chats_per_order,
       AVG(rd.rating) AS avg_rating
FROM SILVER.ORDERS o JOIN region g USING (province)
LEFT JOIN tracking t USING (order_code) LEFT JOIN rated rd USING (order_code)
WHERE o.status IN ('delivered', 'returned') AND o.carrier IS NOT NULL
GROUP BY 1, 2;

-- 6. Reviews: ratings and complaint themes (keyword rules; Vietnamese text) ---------
CREATE OR REPLACE DYNAMIC TABLE GOLD.REVIEW_THEMES
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH tagged AS (
    SELECT r.*, i.category,
           CASE
               WHEN comment ILIKE ANY ('%chậm%', '%trễ%', '%lâu hơn%') THEN 'Giao hàng chậm'
               WHEN comment ILIKE ANY ('%móp%', '%sơ sài%') THEN 'Đóng gói kém'
               WHEN comment ILIKE ANY ('%pin hơi yếu%', '%pin tụt%') THEN 'Pin yếu'
               WHEN comment ILIKE ANY ('%bass hơi yếu%') THEN 'Âm thanh chưa tốt'
               WHEN comment ILIKE ANY ('%mãi mới%') THEN 'Hỗ trợ chậm'
               ELSE 'Khen / trung lập'
           END AS theme
    FROM SILVER.REVIEWS r
    LEFT JOIN SILVER.ORDERS o ON o.channel_order_code = r.channel_order_code
    LEFT JOIN (SELECT DISTINCT order_code, sku, category FROM SILVER.ORDER_ITEMS) i
           ON i.order_code = o.order_code AND i.sku = r.sku
)
SELECT channel, category, theme, COUNT(*) AS reviews, AVG(rating) AS avg_rating
FROM tagged GROUP BY 1, 2, 3;

-- 7. The chatbot itself ------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.CHATBOT_QUALITY
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT intent, COALESCE(policy_topic, '-') AS policy_topic,
       COUNT(*) AS turns,
       AVG(IFF(escalated, 1, 0)) AS escalation_rate,
       AVG(IFF(grounded, 0, 1)) AS ungrounded_rate,
       APPROX_PERCENTILE(latency_ms, 0.5) AS p50_latency_ms,
       APPROX_PERCENTILE(latency_ms, 0.95) AS p95_latency_ms,
       SUM(cost_usd) AS cost_usd
FROM SILVER.CHAT_TURNS GROUP BY 1, 2;

-- 8. Website funnel ---------------------------------------------------------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.WEB_FUNNEL
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT DATE_TRUNC('month', at) AS month,
       COUNT(DISTINCT IFF(event = 'view_product', session_id, NULL)) AS viewing_sessions,
       COUNT(DISTINCT IFF(event = 'add_to_cart', session_id, NULL)) AS cart_sessions,
       COUNT(DISTINCT IFF(event = 'begin_checkout', session_id, NULL)) AS checkout_sessions,
       COUNT_IF(event = 'purchase') AS purchases
FROM SILVER.WEB_EVENTS GROUP BY 1;

-- 9. Win-back list: hashed keys only; marketing joins them back to contacts
--    in its own system, under its own consent rules.
CREATE OR REPLACE DYNAMIC TABLE GOLD.WINBACK_CANDIDATES
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
SELECT customer_key, first_channel, province, revenue_vnd, recency_days,
       IFF(first_order_late, 'Xin lỗi vì giao trễ + voucher', 'Gợi ý sản phẩm liên quan') AS suggested_message,
       IFF(first_channel = 'website', 'website', 'website (voucher chuyển kênh)') AS target_channel
FROM GOLD.CUSTOMER_RFM
WHERE segment = 'One-time, winnable';

-- 10. The loop: every finding becomes an action with its evidence -------------------------
CREATE OR REPLACE DYNAMIC TABLE GOLD.INSIGHT_ACTIONS
  TARGET_LAG = '1 hour' WAREHOUSE = NL_WH
AS
WITH late AS (
    SELECT MAX(IFF(bucket = 'on time', repeat_rate, NULL)) AS on_time,
           MAX(IFF(bucket = 'late', repeat_rate, NULL)) AS late
    FROM GOLD.REPEAT_DRIVERS WHERE driver = 'first_order_delivery'
),
worst_carrier AS (
    SELECT carrier, region, late_rate FROM GOLD.DELIVERY_PERFORMANCE
    WHERE delivered_orders >= 30 QUALIFY ROW_NUMBER() OVER (ORDER BY late_rate DESC) = 1
),
top_gap AS (
    SELECT item, asks, customers FROM GOLD.DEMAND_GAPS WHERE gap_type = 'not_stocked'
    QUALIFY ROW_NUMBER() OVER (ORDER BY asks DESC) <= 3
),
policy_gap AS (
    SELECT policy_topic, turns, escalation_rate FROM GOLD.CHATBOT_QUALITY
    WHERE intent = 'policy_question' AND turns >= 20
    QUALIFY ROW_NUMBER() OVER (ORDER BY escalation_rate DESC) = 1
),
fees AS (
    SELECT channel, fee_share, repeat_rate FROM GOLD.CHANNEL_PERFORMANCE
    WHERE channel <> 'website' QUALIFY ROW_NUMBER() OVER (ORDER BY fee_share DESC) = 1
),
site AS (SELECT repeat_rate FROM GOLD.CHANNEL_PERFORMANCE WHERE channel = 'website'),
winback AS (SELECT COUNT(*) AS n FROM GOLD.WINBACK_CANDIDATES),
price AS (
    SELECT category, budget_vnd, asks, products_in_budget FROM GOLD.PRICE_GAPS
    WHERE asks >= 10 QUALIFY ROW_NUMBER() OVER (ORDER BY asks_per_product DESC) = 1
)
SELECT 'delivery' AS area, 'operations' AS owner, 1 AS priority,
       'Giao trễ làm giảm tỷ lệ mua lại' AS finding,
       'Khách có đơn đầu giao trễ mua lại ' || TO_VARCHAR(ROUND(late.late * 100)) || '% so với '
         || TO_VARCHAR(ROUND(late.on_time * 100)) || '% khi đúng hạn. Chậm nhất: ' || w.carrier || ' ở miền '
         || w.region || ' (' || TO_VARCHAR(ROUND(w.late_rate * 100)) || '% đơn trễ).' AS evidence,
       'Đổi hãng vận chuyển cho khu vực này; chatbot chủ động báo trễ và xin lỗi kèm voucher.' AS action
FROM late, worst_carrier w
UNION ALL
SELECT 'catalogue', 'merchandising', 2, 'Khách hỏi mặt hàng chưa kinh doanh',
       '"' || item || '": ' || asks || ' lượt hỏi từ ' || customers || ' khách.',
       'Cân nhắc nhập hàng; trong lúc chờ, cập nhật chatbot để gợi ý sản phẩm thay thế gần nhất.'
FROM top_gap
UNION ALL
SELECT 'chatbot', 'support', 2, 'Chủ đề chính sách chưa có câu trả lời',
       'Chủ đề "' || policy_topic || '": ' || turns || ' lượt, ' || TO_VARCHAR(ROUND(escalation_rate * 100))
         || '% phải chuyển nhân viên.',
       'Bổ sung mục chính sách cho chủ đề này, rồi thêm ca đánh giá mới vào bộ 56 ca của chatbot.'
FROM policy_gap
UNION ALL
SELECT 'channel', 'marketing', 3, 'Chuyển khách mua lại về website',
       f.channel || ' thu phí ' || TO_VARCHAR(ROUND(f.fee_share * 100)) || '% doanh thu; tỷ lệ mua lại '
         || TO_VARCHAR(ROUND(f.repeat_rate * 100)) || '% so với website ' || TO_VARCHAR(ROUND(s.repeat_rate * 100)) || '%.',
       'Voucher mua lần hai trên website cho khách mua lần đầu trên sàn; chatbot nhắc voucher khi khách tra cứu đơn.'
FROM fees f, site s
UNION ALL
SELECT 'retention', 'marketing', 2, 'Khách mua một lần có thể kéo lại',
       TO_VARCHAR(n) || ' khách mua một lần trong 60–180 ngày qua.',
       'Chiến dịch win-back theo danh sách GOLD.WINBACK_CANDIDATES (khóa băm, không có thông tin liên hệ).'
FROM winback
UNION ALL
SELECT 'pricing', 'merchandising', 3, 'Thiếu sản phẩm ở mức giá khách hỏi',
       category || ' dưới ' || TO_VARCHAR(budget_vnd / 1000000) || ' triệu: ' || asks || ' lượt hỏi, chỉ '
         || products_in_budget || ' sản phẩm còn hàng.',
       'Bổ sung sản phẩm hoặc khuyến mãi ở mức giá này.'
FROM price;
