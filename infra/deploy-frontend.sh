#!/usr/bin/env bash
#
# Deploy the Angular frontend to Azure Static Web Apps.
#
# The free tier serves a single-page application over a global CDN with HTTPS
# and costs nothing, which is the right shape for a compiled Angular bundle.
#
# Prerequisites:
#   az login
#   az extension add --name staticwebapp --upgrade
#   npm install -g @azure/static-web-apps-cli
#
# Usage:
#   BACKEND_URL=https://your-api.azurecontainerapps.io ./infra/deploy-frontend.sh

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-uit-chatbot}"
LOCATION="${LOCATION:-eastasia}"
APP_NAME="${APP_NAME:-uit-chatbot-web}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/../frontend"

if [[ -z "${BACKEND_URL:-}" ]]; then
  echo "Set BACKEND_URL to the deployed Container App URL first." >&2
  echo "  BACKEND_URL=https://your-api.azurecontainerapps.io $0" >&2
  exit 1
fi

# The SPA calls same-origin /api/* paths. In development the Angular dev server
# proxies those to localhost; in production Static Web Apps rewrites them to the
# Container App. Writing the rule here keeps the frontend free of any hardcoded
# backend hostname, so the same bundle works in both places.
cat > "$FRONTEND_DIR/staticwebapp.config.json" <<CONFIG
{
  "routes": [
    {
      "route": "/api/*",
      "rewrite": "$BACKEND_URL/api/*"
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "*.{css,js,ico,png,jpg,svg,woff2}"]
  },
  "globalHeaders": {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin"
  }
}
CONFIG

echo "==> Building Angular bundle"
( cd "$FRONTEND_DIR" && npm ci && npx ng build --configuration production )

OUTPUT_DIR="$FRONTEND_DIR/dist/support-chat/browser"
if [[ ! -d "$OUTPUT_DIR" ]]; then
  # Angular's output layout has moved between versions; fail loudly rather than
  # uploading an empty directory and leaving a blank page to debug in the browser.
  echo "Build output not found at $OUTPUT_DIR" >&2
  echo "Check the outputPath in angular.json and adjust this script." >&2
  exit 1
fi

echo "==> Static Web App"
az staticwebapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Free \
  --output none 2>/dev/null || echo "    (already exists, reusing)"

TOKEN="$(az staticwebapp secrets list --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query 'properties.apiKey' --output tsv)"

echo "==> Uploading"
swa deploy "$OUTPUT_DIR" --deployment-token "$TOKEN" --env production

HOSTNAME="$(az staticwebapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query defaultHostname --output tsv)"

cat <<SUMMARY

Frontend deployed.

  Chat        https://$HOSTNAME
  Operations  https://$HOSTNAME/admin

Now allow this origin on the backend, or every request will fail CORS:

  az containerapp update --name uit-chatbot-api --resource-group $RESOURCE_GROUP \\
    --set-env-vars 'CORS_ORIGINS=["https://$HOSTNAME"]'

SUMMARY
