-- One-time Snowflake setup for LLM_PROVIDER=cortex.
--
-- Run the whole script in a Snowsight SQL worksheet, signed in as the account's
-- first user (who holds ACCOUNTADMIN on a trial). It is safe to re-run except
-- for the last statement, which mints a new token each time.
--
-- The backend never signs in as a person. It authenticates as CHATBOT_SVC, a
-- service user that holds one role granting one thing: calling Cortex models.
-- If its token leaks, the damage is someone spending Cortex credits, not
-- reading or changing any data in the account.

USE ROLE ACCOUNTADMIN;

-- 1. Claude is not hosted in every Snowflake region. Without this, a request
--    for a model the account's own region lacks fails outright; with it, Cortex
--    routes the request to a region that serves the model. No egress charge.
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- 2. A least-privilege role. CORTEX_USER is the database role that permits
--    calling Cortex AI; the chatbot's token will be restricted to this role.
CREATE ROLE IF NOT EXISTS CHATBOT_APP;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE CHATBOT_APP;

-- 3. A service user: no password, cannot sign in to Snowsight, exists only to
--    hold the backend's token.
CREATE USER IF NOT EXISTS CHATBOT_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = CHATBOT_APP
  COMMENT = 'Northlight chatbot backend. Calls Cortex models only.';
GRANT ROLE CHATBOT_APP TO USER CHATBOT_SVC;

-- 4. Programmatic access tokens only work for a user under a network policy.
--    Neither Azure Container Apps (on the Consumption plan) nor a developer
--    laptop has a stable outbound IP, so the policy admits any IPv4 address.
--    The token itself remains the credential, and it can do nothing but call
--    Cortex (step 2). For anything beyond a demo, narrow this to fixed egress
--    IPs, as with Atlas Network Access.
CREATE DATABASE IF NOT EXISTS CHATBOT_ADMIN
  COMMENT = 'Holds security objects for the chatbot. No application data.';
CREATE SCHEMA IF NOT EXISTS CHATBOT_ADMIN.SECURITY;

CREATE NETWORK RULE IF NOT EXISTS CHATBOT_ADMIN.SECURITY.ANY_IPV4
  MODE = INGRESS
  TYPE = IPV4
  VALUE_LIST = ('0.0.0.0/0');

CREATE NETWORK POLICY IF NOT EXISTS CHATBOT_ANY_IP
  ALLOWED_NETWORK_RULE_LIST = ('CHATBOT_ADMIN.SECURITY.ANY_IPV4');

ALTER USER CHATBOT_SVC SET NETWORK_POLICY = CHATBOT_ANY_IP;

-- 5. The token. DAYS_TO_EXPIRY defaults to 15, which is shorter than a 30-day
--    trial: a demo set up on day one would stop working mid-trial. 45 outlasts
--    the trial.
--
--    The result row's TOKEN_SECRET column is the only place the token is ever
--    shown. Copy it into SNOWFLAKE_PAT in backend/.env immediately. Re-running
--    this statement creates a second token rather than showing the first again;
--    drop an unwanted one with:
--      ALTER USER CHATBOT_SVC REMOVE PROGRAMMATIC ACCESS TOKEN CHATBOT_BACKEND;
ALTER USER CHATBOT_SVC ADD PROGRAMMATIC ACCESS TOKEN CHATBOT_BACKEND
  ROLE_RESTRICTION = 'CHATBOT_APP'
  DAYS_TO_EXPIRY = 45
  COMMENT = 'Backend credential for Cortex. Stored in backend/.env as SNOWFLAKE_PAT.';
