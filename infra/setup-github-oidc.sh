#!/usr/bin/env bash
#
# One-time setup so GitHub Actions can deploy to Azure without any stored secret.
#
# Creates a user-assigned managed identity and a federated credential that
# trusts GitHub's OIDC token for pushes to main of this repository only. A
# managed identity is used rather than an app registration because university
# tenants usually forbid students from registering applications.
#
# Roles, the least that the workflow needs:
#   AcrPush      on the registry        push images
#   Contributor  on the resource group  update the Container App, the job, and
#                                       read the Static Web App deployment token
#
# Usage: ./infra/setup-github-oidc.sh
# Then add the three printed values as repository variables in GitHub
# (Settings > Secrets and variables > Actions > Variables), or run:
#   gh variable set AZURE_CLIENT_ID --body <value>   (and the same for the others)

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-uit-chatbot}"
LOCATION="${LOCATION:-eastasia}"
REGISTRY="${REGISTRY:-acruitchatbot6435}"
IDENTITY="${IDENTITY:-id-github-deploy}"
REPO="${REPO:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
BRANCH="${BRANCH:-main}"

az identity create -n "$IDENTITY" -g "$RESOURCE_GROUP" -l "$LOCATION" --output none
PRINCIPAL="$(az identity show -n "$IDENTITY" -g "$RESOURCE_GROUP" --query principalId -o tsv)"
CLIENT_ID="$(az identity show -n "$IDENTITY" -g "$RESOURCE_GROUP" --query clientId -o tsv)"
SUBSCRIPTION="$(az account show --query id -o tsv)"
TENANT="$(az account show --query tenantId -o tsv)"

az identity federated-credential create -n "github-$BRANCH" --identity-name "$IDENTITY" -g "$RESOURCE_GROUP" \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:$REPO:ref:refs/heads/$BRANCH" \
  --audiences "api://AzureADTokenExchange" --output none

assign() {
  az role assignment create --role "$1" --assignee-object-id "$PRINCIPAL" \
    --assignee-principal-type ServicePrincipal --scope "$2" --output none 2>/dev/null || true
}
assign AcrPush "$(az acr show -n "$REGISTRY" --query id -o tsv)"
assign Contributor "/subscriptions/$SUBSCRIPTION/resourceGroups/$RESOURCE_GROUP"

cat <<SUMMARY
GitHub OIDC ready for $REPO (branch $BRANCH).
Add these repository variables in GitHub:
  AZURE_CLIENT_ID        $CLIENT_ID
  AZURE_TENANT_ID        $TENANT
  AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION
SUMMARY
