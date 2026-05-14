#!/usr/bin/env bash
# Promote V2 to 100% of traffic. V1 stays deployed (deactivated) for instant rollback.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

echo "=== Promoting $APP_NAME--v2 to 100% traffic ==="
az containerapp ingress traffic set \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --revision-weight "$APP_NAME--v2=100" "$APP_NAME--v1=0" \
    -o none

echo "Done. Current weights:"
az containerapp ingress traffic show \
    --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" -o table
