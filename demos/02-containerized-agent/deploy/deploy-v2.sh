#!/usr/bin/env bash
# Build the V2 image, push to ACR, and deploy as a canary revision
# (V1 keeps 90% of traffic, V2 gets 10%).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

IMAGE="$ACR_NAME.azurecr.io/$APP_NAME:v2"

echo "=== 1/3  Build and push image: $IMAGE ==="
az acr build \
    --registry "$ACR_NAME" \
    --image "$APP_NAME:v2" \
    --file "$SCRIPT_DIR/../Dockerfile" \
    "$SCRIPT_DIR/.."

echo "=== 2/3  Adding new revision $APP_NAME--v2 (V2 image) ==="
az containerapp update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --image "$IMAGE" \
    --revision-suffix v2 \
    --set-env-vars \
        "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT" \
        "AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT" \
        "AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION" \
        "AZURE_CLIENT_ID=$UAMI_CLIENT_ID" \
        "AGENT_VERSION=v2" \
        "APPLICATIONINSIGHTS_CONNECTION_STRING=$APPLICATIONINSIGHTS_CONNECTION_STRING" \
    -o none

echo "=== 3/3  Splitting traffic: V1=90% / V2=10% (canary) ==="
az containerapp ingress traffic set \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --revision-weight "$APP_NAME--v1=90" "$APP_NAME--v2=10" \
    -o none

FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "=== Canary in place ==="
echo "  https://$FQDN  ->  V1 90% / V2 10%"
echo "  Bake for ~30 min, then run promote-v2.sh or rollback-v1.sh"
