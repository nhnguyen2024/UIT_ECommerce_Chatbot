-- Bronze: the raw JSON exactly as it landed in Azure Data Lake, one table per
-- source dataset, with the file it came from and when it was loaded.
--
-- Bronze never interprets anything. If a marketplace changes a field name, the
-- raw record is still here and Silver is the only place that needs fixing.
--
-- {{ADLS_ACCOUNT}}, {{ADLS_CONTAINER}} and {{ADLS_SAS}} are filled in by
-- analytics/pipeline.py from analytics/.env; the SAS token is read + list only.

USE ROLE NL_PIPELINE;
USE WAREHOUSE NL_WH;
USE DATABASE NORTHLIGHT_DW;

CREATE FILE FORMAT IF NOT EXISTS RAW_STAGE.NDJSON
  TYPE = JSON
  STRIP_OUTER_ARRAY = FALSE
  COMMENT = 'One JSON document per line';

-- A SAS-token stage rather than a storage integration: an integration needs a
-- tenant administrator to consent to Snowflake's Azure application, which a
-- university tenant does not grant to students.
CREATE OR REPLACE STAGE RAW_STAGE.LAKE
  URL = 'azure://{{ADLS_ACCOUNT}}.blob.core.windows.net/{{ADLS_CONTAINER}}/raw/'
  CREDENTIALS = (AZURE_SAS_TOKEN = '{{ADLS_SAS}}')
  FILE_FORMAT = RAW_STAGE.NDJSON;

-- One generic shape for every bronze table.
CREATE TABLE IF NOT EXISTS BRONZE.RAW_EVENTS (
    source      STRING,          -- oms, shopee, lazada, tiktok_shop, website, chatbot, app
    dataset     STRING,          -- orders, reviews, listing_stats, events, turns, ...
    extract_dt  DATE,            -- the dt= partition the file sat in
    file_name   STRING,
    row_number  NUMBER,
    payload     VARIANT,
    loaded_at   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Load everything under raw/. COPY remembers which files it has loaded (for 64
-- days), so re-running only picks up new files: this is the incremental load.
COPY INTO BRONZE.RAW_EVENTS (source, dataset, extract_dt, file_name, row_number, payload)
FROM (
    SELECT
        REGEXP_SUBSTR(METADATA$FILENAME, 'source=([^/]+)', 1, 1, 'e'),
        SPLIT_PART(REGEXP_SUBSTR(METADATA$FILENAME, 'source=[^/]+/([^/]+)', 1, 1, 'e'), '/', 1),
        TRY_TO_DATE(REGEXP_SUBSTR(METADATA$FILENAME, 'dt=([0-9-]+)', 1, 1, 'e')),
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        $1
    FROM @RAW_STAGE.LAKE
)
PATTERN = '.*[.]json'
ON_ERROR = 'CONTINUE';

-- Keep new files flowing in without anyone running COPY by hand. Suspended by
-- default; pipeline.py resumes it after the first successful load.
CREATE OR REPLACE TASK BRONZE.LOAD_FROM_LAKE
  WAREHOUSE = NL_WH
  SCHEDULE = 'USING CRON 0 * * * * Asia/Ho_Chi_Minh'
  COMMENT = 'Hourly: load new files from Azure Data Lake into bronze'
AS
COPY INTO BRONZE.RAW_EVENTS (source, dataset, extract_dt, file_name, row_number, payload)
FROM (
    SELECT
        REGEXP_SUBSTR(METADATA$FILENAME, 'source=([^/]+)', 1, 1, 'e'),
        SPLIT_PART(REGEXP_SUBSTR(METADATA$FILENAME, 'source=[^/]+/([^/]+)', 1, 1, 'e'), '/', 1),
        TRY_TO_DATE(REGEXP_SUBSTR(METADATA$FILENAME, 'dt=([0-9-]+)', 1, 1, 'e')),
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        $1
    FROM @RAW_STAGE.LAKE
)
PATTERN = '.*[.]json'
ON_ERROR = 'CONTINUE';
