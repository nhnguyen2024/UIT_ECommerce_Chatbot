#!/usr/bin/env bash
#
# Deploy the FastAPI backend to Azure Container Apps.
#
# Container Apps rather than App Service or Functions, for three reasons that
# matter to this application: it scales to zero, so an idle demo costs nothing
# against a student credit; it streams responses without buffering, which a
# Server-Sent Events endpoint requires; and it takes a container image directly,
# so what runs in Azure is what was built locally.
#
# Prerequisites:
#   az login
#   az extension add --name containerapp --upgrade
#
# Usage:
#   ./infra/deploy-backend.sh
#
# Secrets are read from backend/.env and passed as Container Apps secrets. They
# are never baked into the image.

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-uit-chatbot}"
LOCATION="${LOCATION:-southeastasia}"
ENVIRONMENT="${ENVIRONMENT:-cae-uit-chatbot}"
APP_NAME="${APP_NAME:-uit-chatbot-api}"
REGISTRY="${REGISTRY:-acruitchatbot$RANDOM}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
ENV_FILE="$BACKEND_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy backend/.env.example and fill it in first." >&2
  exit 1
fi

# Read the two secrets we need without sourcing the file, so a stray command in
# .env cannot execute.
read_env() {
  grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^"//; s/"$//'
}

require() {
  # Missing and left-as-template values both count as unset: deploying the
  # placeholder from .env.example would only fail later, on the first chat turn.
  local name="$1" value="$2"
  if [[ -z "$value" || "$value" == *"..."* || "$value" == *"ORGNAME-ACCOUNTNAME"* ]]; then
    echo "$name must be set in $ENV_FILE" >&2
    exit 1
  fi
}

MONGODB_URI="$(read_env MONGODB_URI)"
require MONGODB_URI "$MONGODB_URI"

# `|| true` because read_env fails when a key is absent, and under set -e that
# would end the script silently for an older .env written before this setting.
LLM_PROVIDER="$(read_env LLM_PROVIDER || true)"
LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"

# Secrets go in as Container Apps secrets and reach the app only through
# secretref, so they never appear in the app's plain environment configuration.
SECRETS=("mongodb-uri=$MONGODB_URI")
PROVIDER_ENV=("LLM_PROVIDER=$LLM_PROVIDER")

case "$LLM_PROVIDER" in
  anthropic)
    ANTHROPIC_API_KEY="$(read_env ANTHROPIC_API_KEY)"
    require ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
    SECRETS+=("anthropic-key=$ANTHROPIC_API_KEY")
    PROVIDER_ENV+=("ANTHROPIC_API_KEY=secretref:anthropic-key")
    ;;
  cortex)
    SNOWFLAKE_ACCOUNT_URL="$(read_env SNOWFLAKE_ACCOUNT_URL)"
    SNOWFLAKE_PAT="$(read_env SNOWFLAKE_PAT)"
    require SNOWFLAKE_ACCOUNT_URL "$SNOWFLAKE_ACCOUNT_URL"
    require SNOWFLAKE_PAT "$SNOWFLAKE_PAT"
    SECRETS+=("snowflake-pat=$SNOWFLAKE_PAT")
    PROVIDER_ENV+=(
      "SNOWFLAKE_ACCOUNT_URL=$SNOWFLAKE_ACCOUNT_URL"
      "SNOWFLAKE_PAT=secretref:snowflake-pat"
    )
    ;;
  azure_openai)
    AZURE_OPENAI_ENDPOINT="$(read_env AZURE_OPENAI_ENDPOINT)"
    AZURE_OPENAI_API_KEY="$(read_env AZURE_OPENAI_API_KEY)"
    require AZURE_OPENAI_ENDPOINT "$AZURE_OPENAI_ENDPOINT"
    require AZURE_OPENAI_API_KEY "$AZURE_OPENAI_API_KEY"
    if [[ "$AZURE_OPENAI_ENDPOINT" == *"RESOURCE.openai.azure.com"* ]]; then
      echo "AZURE_OPENAI_ENDPOINT must be set in $ENV_FILE" >&2
      exit 1
    fi
    SECRETS+=("azure-openai-key=$AZURE_OPENAI_API_KEY")
    PROVIDER_ENV+=(
      "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT"
      "AZURE_OPENAI_API_KEY=secretref:azure-openai-key"
    )
    ;;
  *)
    echo "LLM_PROVIDER must be anthropic, cortex or azure_openai, not '$LLM_PROVIDER'" >&2
    exit 1
    ;;
esac

# Model names, effort and prices follow .env, because they differ by provider:
# for Azure OpenAI the model names are deployment names. Hard-coding them here
# once deployed Claude model names against an Azure endpoint. Absent values
# fall back to the app's own defaults by simply not being set.
for NAME in AGENT_MODEL CLASSIFIER_MODEL AGENT_EFFORT \
            PRICE_INPUT_PER_MTOK PRICE_OUTPUT_PER_MTOK PRICE_CACHE_READ_PER_MTOK; do
  VALUE="$(read_env "$NAME" || true)"
  if [[ -n "$VALUE" ]]; then
    PROVIDER_ENV+=("$NAME=$VALUE")
  fi
done

# Whatever app.agent.probe reported for this provider, carried over unchanged.
# Absent means true, matching the app's own default; an empty string would not
# parse as a boolean and the app would refuse to start.
LLM_STRICT_TOOLS="$(read_env LLM_STRICT_TOOLS || true)"
LLM_EFFORT="$(read_env LLM_EFFORT || true)"
PROVIDER_ENV+=(
  "LLM_STRICT_TOOLS=${LLM_STRICT_TOOLS:-true}"
  "LLM_EFFORT=${LLM_EFFORT:-true}"
)

echo "==> Resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Container registry: $REGISTRY"
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$REGISTRY" \
  --sku Basic \
  --admin-enabled true \
  --output none

# az acr build builds in Azure rather than locally. That avoids pushing a large
# image over a home connection, and it produces a linux/amd64 image regardless
# of whether the developer is on an Apple Silicon machine, which is the usual
# cause of "exec format error" on first deploy.
echo "==> Building image in Azure (tag: $IMAGE_TAG)"
az acr build \
  --registry "$REGISTRY" \
  --image "chatbot-api:$IMAGE_TAG" \
  --file "$BACKEND_DIR/Dockerfile" \
  "$BACKEND_DIR"

echo "==> Container Apps environment"
az containerapp env create \
  --name "$ENVIRONMENT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

REGISTRY_SERVER="$(az acr show --name "$REGISTRY" --query loginServer --output tsv)"
REGISTRY_PASSWORD="$(az acr credential show --name "$REGISTRY" --query 'passwords[0].value' --output tsv)"

echo "==> Deploying $APP_NAME"
az containerapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ENVIRONMENT" \
  --image "$REGISTRY_SERVER/chatbot-api:$IMAGE_TAG" \
  --registry-server "$REGISTRY_SERVER" \
  --registry-username "$REGISTRY" \
  --registry-password "$REGISTRY_PASSWORD" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 2 \
  --cpu 0.5 --memory 1.0Gi \
  --secrets "${SECRETS[@]}" \
  --env-vars \
    "${PROVIDER_ENV[@]}" \
    "MONGODB_URI=secretref:mongodb-uri" \
    "MONGODB_DB=uit_ecommerce_chatbot" \
    "EMBEDDING_MODE=auto" \
  --output none

FQDN="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"

cat <<SUMMARY

Backend deployed.

  URL     https://$FQDN
  Health  https://$FQDN/health
  Ready   https://$FQDN/ready
  Docs    https://$FQDN/docs

Two things remain:

1. Add the frontend origin to CORS_ORIGINS, or the browser will block every
   request:

     az containerapp update --name $APP_NAME --resource-group $RESOURCE_GROUP \\
       --set-env-vars 'CORS_ORIGINS=["https://YOUR-FRONTEND.azurestaticapps.net"]'

2. Allow Azure to reach Atlas. Under Atlas Network Access, add 0.0.0.0/0 for a
   demo, since Container Apps replicas do not have a stable outbound IP on the
   Consumption plan. For anything beyond a demo, use a VNet-integrated
   environment with a NAT gateway and allow-list that address instead.

SUMMARY
