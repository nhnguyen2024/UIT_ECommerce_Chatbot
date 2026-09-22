-- One-time setup for the analytics pipeline. Run once, as ACCOUNTADMIN, in a
-- Snowsight SQL worksheet (select all, then Run).
--
-- It creates a small warehouse, the NORTHLIGHT_DW database with one schema per
-- medallion layer, and a role for the pipeline, and it lets the existing
-- service user CHATBOT_SVC use that role. The last statement issues a new
-- programmatic access token: copy it from the result into analytics/.env as
-- SNOWFLAKE_PAT='...'. Never paste it into a chat.
--
-- It also revokes the token that was exposed in a chat earlier.

USE ROLE ACCOUNTADMIN;

-- Compute: extra-small, suspends after 60 s idle, so it bills only while working.
CREATE WAREHOUSE IF NOT EXISTS NL_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Northlight analytics pipeline';

-- Storage: one database, one schema per layer.
CREATE DATABASE IF NOT EXISTS NORTHLIGHT_DW COMMENT = 'Northlight analytics: bronze, silver, gold';
CREATE SCHEMA IF NOT EXISTS NORTHLIGHT_DW.RAW_STAGE COMMENT = 'External stage over Azure Data Lake';
CREATE SCHEMA IF NOT EXISTS NORTHLIGHT_DW.BRONZE COMMENT = 'Raw JSON exactly as landed, plus load metadata';
CREATE SCHEMA IF NOT EXISTS NORTHLIGHT_DW.SILVER COMMENT = 'Typed, cleaned, unified across channels';
CREATE SCHEMA IF NOT EXISTS NORTHLIGHT_DW.GOLD COMMENT = 'Analytics marts and recommended actions';

-- The pipeline's role: owns everything inside NORTHLIGHT_DW, nothing outside it.
CREATE ROLE IF NOT EXISTS NL_PIPELINE;
GRANT USAGE, OPERATE ON WAREHOUSE NL_WH TO ROLE NL_PIPELINE;
GRANT OWNERSHIP ON DATABASE NORTHLIGHT_DW TO ROLE NL_PIPELINE COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SCHEMAS IN DATABASE NORTHLIGHT_DW TO ROLE NL_PIPELINE COPY CURRENT GRANTS;
GRANT EXECUTE TASK ON ACCOUNT TO ROLE NL_PIPELINE;
-- Sentiment and classification functions for review text, if the account allows them.
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE NL_PIPELINE;

-- Let the existing service user run the pipeline, and let you browse the results.
GRANT ROLE NL_PIPELINE TO USER CHATBOT_SVC;
ALTER USER CHATBOT_SVC SET DEFAULT_ROLE = NL_PIPELINE, DEFAULT_WAREHOUSE = NL_WH;
GRANT ROLE NL_PIPELINE TO ROLE SYSADMIN;

-- Issue the pipeline's token. Copy the token_secret value from the result into
-- analytics/.env as SNOWFLAKE_PAT='...'. It is shown only once.
ALTER USER CHATBOT_SVC ADD PROGRAMMATIC ACCESS TOKEN NL_PIPELINE_TOKEN
  ROLE_RESTRICTION = 'NL_PIPELINE'
  DAYS_TO_EXPIRY = 60
  COMMENT = 'Analytics pipeline (analytics/.env)';

-- Revoke the token that was exposed in a chat. Last on purpose: if it was
-- already removed, this line errors and nothing above is affected.
ALTER USER CHATBOT_SVC REMOVE PROGRAMMATIC ACCESS TOKEN CHATBOT_BACKEND;
