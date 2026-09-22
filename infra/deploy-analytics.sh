#!/usr/bin/env bash
#
# Deploy the analytics loop as a scheduled Azure Container Apps Job.
#
# What it creates, and why:
#   * Key Vault          the pipeline's secrets (MongoDB URI, Snowflake token,
#                        the read-only SAS the Snowflake stage uses). RBAC mode:
#                        access is granted per identity and every read is logged.
#   * Managed identity   the job's Azure identity. It may read those secrets,
#                        write to the data lake and pull the image. No storage
#                        key or registry password is handed to the job.
#   * Container Apps Job runs `pipeline.py scheduled` every hour (and on demand):
#                        extract -> upload -> COPY into bronze -> refresh silver
#                        and gold -> write gold back to MongoDB `insights`.
#
# Prerequisites: az login; the backend already deployed (its registry is reused);
# analytics/.env and backend/.env filled in.
#
# Usage:
#   REGISTRY=acruitchatbot6435 ./infra/deploy-analytics.sh
#   ./infra/deploy-analytics.sh run        # start one execution now
#
# SQL changes need no separate step: each run compares the SQL in its image with
# the version recorded in Snowflake and redeploys when they differ.

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-uit-chatbot}"
LOCATION="${LOCATION:-eastasia}"
# A separate, standard environment: the backend's environment is an "express"
# one, which does not support jobs (ExpressEnvironmentResourceNotSupported), and
# recent CLI versions create express environments unless told otherwise.
ENVIRONMENT="${ENVIRONMENT:-cae-uit-jobs}"
REGISTRY="${REGISTRY:-acruitchatbot6435}"
VAULT="${VAULT:-kv-uit-chatbot-6435}"
IDENTITY="${IDENTITY:-id-nl-analytics}"
JOB="${JOB:-nl-analytics-job}"
SCHEDULE="${SCHEDULE:-5 * * * *}"          # minute 5 of every hour, UTC
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYTICS_DIR="$SCRIPT_DIR/../analytics"

start_execution() {
  local args=("$@")
  az containerapp job start --name "$JOB" --resource-group "$RESOURCE_GROUP" \
    ${args[@]+"${args[@]}"} --query name --output tsv
}

case "${1:-deploy}" in
  run) start_execution; exit 0 ;;
  deploy) ;;
  *) echo "usage: $0 [deploy|run]" >&2; exit 1 ;;
esac

# Read a value without sourcing the file, so a stray command in .env cannot run.
read_env() {
  grep -E "^$2=" "$1" | head -1 | cut -d= -f2- | sed "s/^['\"]//; s/['\"]$//"
}
A_ENV="$ANALYTICS_DIR/.env"
B_ENV="$ANALYTICS_DIR/../backend/.env"

SUBSCRIPTION="$(az account show --query id -o tsv)"
ME="$(az ad signed-in-user show --query id -o tsv)"
RG_ID="/subscriptions/$SUBSCRIPTION/resourceGroups/$RESOURCE_GROUP"

echo "==> Managed identity: $IDENTITY"
az identity create -n "$IDENTITY" -g "$RESOURCE_GROUP" -l "$LOCATION" --output none
ID_RESOURCE="$(az identity show -n "$IDENTITY" -g "$RESOURCE_GROUP" --query id -o tsv)"
ID_PRINCIPAL="$(az identity show -n "$IDENTITY" -g "$RESOURCE_GROUP" --query principalId -o tsv)"
ID_CLIENT="$(az identity show -n "$IDENTITY" -g "$RESOURCE_GROUP" --query clientId -o tsv)"

echo "==> Key Vault: $VAULT"
if ! az keyvault show -n "$VAULT" -g "$RESOURCE_GROUP" --output none 2>/dev/null; then
  az keyvault create -n "$VAULT" -g "$RESOURCE_GROUP" -l "$LOCATION" \
    --enable-rbac-authorization true --retention-days 7 --output none
fi
VAULT_ID="$(az keyvault show -n "$VAULT" -g "$RESOURCE_GROUP" --query id -o tsv)"
VAULT_URI="$(az keyvault show -n "$VAULT" -g "$RESOURCE_GROUP" --query properties.vaultUri -o tsv)"

assign() {  # role, principal, scope, principal type
  az role assignment create --role "$1" --assignee-object-id "$2" --assignee-principal-type "$4" \
    --scope "$3" --output none 2>/dev/null || true
}
STORAGE_ID="$(az storage account show -n "$(read_env "$A_ENV" ADLS_ACCOUNT)" -g "$RESOURCE_GROUP" --query id -o tsv)"
REGISTRY_ID="$(az acr show -n "$REGISTRY" --query id -o tsv)"
assign "Key Vault Secrets Officer" "$ME" "$VAULT_ID" User             # you: write secrets
assign "Key Vault Secrets User" "$ID_PRINCIPAL" "$VAULT_ID" ServicePrincipal
assign "Storage Blob Data Contributor" "$ID_PRINCIPAL" "$STORAGE_ID" ServicePrincipal
assign "AcrPull" "$ID_PRINCIPAL" "$REGISTRY_ID" ServicePrincipal

echo "==> Secrets into Key Vault (values are never printed)"
put_secret() {
  local name="$1" value="$2" tries=0
  [[ -n "$value" ]] || { echo "missing value for $name" >&2; exit 1; }
  # Role assignments take a minute to reach Key Vault; retry until they do.
  until az keyvault secret set --vault-name "$VAULT" -n "$name" --value "$value" --output none 2>/dev/null; do
    tries=$((tries + 1)); [[ $tries -lt 20 ]] || { echo "cannot write $name to $VAULT" >&2; exit 1; }
    sleep 15
  done
}
put_secret mongodb-uri "$(read_env "$B_ENV" MONGODB_URI)"
put_secret snowflake-pat "$(read_env "$A_ENV" SNOWFLAKE_PAT)"
put_secret adls-sas "$(read_env "$A_ENV" ADLS_SAS)"

echo "==> Image (built locally for linux/amd64; ACR Tasks are blocked on student subscriptions)"
REGISTRY_SERVER="$(az acr show -n "$REGISTRY" --query loginServer -o tsv)"
az acr login -n "$REGISTRY" --output none
docker buildx build --platform linux/amd64 --tag "$REGISTRY_SERVER/nl-analytics:$IMAGE_TAG" \
  --file "$ANALYTICS_DIR/Dockerfile" --push "$ANALYTICS_DIR"

SECRETS=(
  "mongodb-uri=keyvaultref:${VAULT_URI}secrets/mongodb-uri,identityref:$ID_RESOURCE"
  "snowflake-pat=keyvaultref:${VAULT_URI}secrets/snowflake-pat,identityref:$ID_RESOURCE"
  "adls-sas=keyvaultref:${VAULT_URI}secrets/adls-sas,identityref:$ID_RESOURCE"
)
ENV_VARS=(
  "MONGODB_URI=secretref:mongodb-uri"
  "SNOWFLAKE_PAT=secretref:snowflake-pat"
  "ADLS_SAS=secretref:adls-sas"
  "MONGODB_DB=$(read_env "$B_ENV" MONGODB_DB || echo uit_ecommerce_chatbot)"
  "SNOWFLAKE_ACCOUNT_URL=$(read_env "$A_ENV" SNOWFLAKE_ACCOUNT_URL)"
  "SNOWFLAKE_USER=$(read_env "$A_ENV" SNOWFLAKE_USER)"
  "SNOWFLAKE_ROLE=$(read_env "$A_ENV" SNOWFLAKE_ROLE)"
  "SNOWFLAKE_WAREHOUSE=$(read_env "$A_ENV" SNOWFLAKE_WAREHOUSE)"
  "ADLS_ACCOUNT=$(read_env "$A_ENV" ADLS_ACCOUNT)"
  "ADLS_CONTAINER=$(read_env "$A_ENV" ADLS_CONTAINER)"
  "AZURE_CLIENT_ID=$ID_CLIENT"               # which identity DefaultAzureCredential uses
)

echo "==> Container Apps environment for jobs: $ENVIRONMENT"
az containerapp env show -n "$ENVIRONMENT" -g "$RESOURCE_GROUP" --output none 2>/dev/null ||
  az containerapp env create -n "$ENVIRONMENT" -g "$RESOURCE_GROUP" -l "$LOCATION" \
    --environment-mode WorkloadProfiles --logs-destination none --output none

echo "==> Container Apps Job: $JOB (cron '$SCHEDULE' UTC)"
if az containerapp job show -n "$JOB" -g "$RESOURCE_GROUP" --output none 2>/dev/null; then
  az containerapp job secret set -n "$JOB" -g "$RESOURCE_GROUP" --secrets "${SECRETS[@]}" --output none
  az containerapp job update -n "$JOB" -g "$RESOURCE_GROUP" \
    --image "$REGISTRY_SERVER/nl-analytics:$IMAGE_TAG" --cron-expression "$SCHEDULE" \
    --set-env-vars "${ENV_VARS[@]}" --output none
else
  az containerapp job create -n "$JOB" -g "$RESOURCE_GROUP" --environment "$ENVIRONMENT" \
    --trigger-type Schedule --cron-expression "$SCHEDULE" \
    --replica-timeout 1200 --replica-retry-limit 1 --parallelism 1 --replica-completion-count 1 \
    --image "$REGISTRY_SERVER/nl-analytics:$IMAGE_TAG" --cpu 0.5 --memory 1.0Gi \
    --mi-user-assigned "$ID_RESOURCE" --registry-server "$REGISTRY_SERVER" --registry-identity "$ID_RESOURCE" \
    --secrets "${SECRETS[@]}" --env-vars "${ENV_VARS[@]}" --output none
fi

echo "Done. Start a run now with: $0 run"
