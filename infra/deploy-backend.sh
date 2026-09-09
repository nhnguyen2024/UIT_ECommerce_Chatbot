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

ANTHROPIC_API_KEY="$(read_env ANTHROPIC_API_KEY)"
MONGODB_URI="$(read_env MONGODB_URI)"

if [[ -z "$ANTHROPIC_API_KEY" || -z "$MONGODB_URI" ]]; then
  echo "ANTHROPIC_API_KEY and MONGODB_URI must both be set in $ENV_FILE" >&2
  exit 1
fi

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
  --secrets "anthropic-key=$ANTHROPIC_API_KEY" "mongodb-uri=$MONGODB_URI" \
  --env-vars \
    "ANTHROPIC_API_KEY=secretref:anthropic-key" \
    "MONGODB_URI=secretref:mongodb-uri" \
    "MONGODB_DB=uit_ecommerce_chatbot" \
    "EMBEDDING_MODE=auto" \
    "AGENT_MODEL=claude-opus-5" \
    "CLASSIFIER_MODEL=claude-haiku-4-5" \
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
