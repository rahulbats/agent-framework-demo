#!/usr/bin/env bash
# Build the V1 image, push to ACR, and create the Container App with 100% traffic on V1.
# Idempotent: if the app already exists, this script just updates it to point at the V1 image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

IMAGE="$ACR_NAME.azurecr.io/$APP_NAME:v1"

echo "=== 1/3  Build and push image: $IMAGE ==="
az acr build \
    --registry "$ACR_NAME" \
    --image "$APP_NAME:v1" \
    --file "$SCRIPT_DIR/../Dockerfile" \
    "$SCRIPT_DIR/.."

# --- Common create/update args ---
COMMON_ENV_VARS=(
    "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT"
    "AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT"
    "AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION"
    "AZURE_CLIENT_ID=$UAMI_CLIENT_ID"
    "AGENT_VERSION=v1"
    "APPLICATIONINSIGHTS_CONNECTION_STRING=$APPLICATIONINSIGHTS_CONNECTION_STRING"
)

if az containerapp show -g "$RESOURCE_GROUP" -n "$APP_NAME" >/dev/null 2>&1; then
    echo "=== 2/3  App exists. Updating to V1 image and re-pinning 100% traffic. ==="
    az containerapp update \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --image "$IMAGE" \
        --revision-suffix v1 \
        --set-env-vars "${COMMON_ENV_VARS[@]}" \
        -o none
else
    echo "=== 2/3  Creating Container App (multi-revision, UAMI auth to ACR + AOAI) ==="
    az containerapp create \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --environment "$ACA_ENV" \
        --image "$IMAGE" \
        --revision-suffix v1 \
        --revisions-mode multiple \
        --target-port 8080 \
        --ingress external \
        --user-assigned "$UAMI_RESOURCE_ID" \
        --registry-server "$ACR_NAME.azurecr.io" \
        --registry-identity "$UAMI_RESOURCE_ID" \
        --cpu 0.5 --memory 1.0Gi \
        --min-replicas 1 --max-replicas 3 \
        --env-vars "${COMMON_ENV_VARS[@]}" \
        -o none
fi

echo "=== 3/3  Pinning 100% traffic to revision $APP_NAME--v1 ==="
az containerapp ingress traffic set \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --revision-weight "$APP_NAME--v1=100" \
    -o none

FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "=== V1 deployed (100% traffic) ==="
echo "  Health: https://$FQDN/health"
echo "  Invoke: curl -X POST https://$FQDN/invoke -H 'content-type: application/json' \\"
echo "             -d '{\"input\":\"List the submission documents and tell me what is in the loss run.\"}'"
