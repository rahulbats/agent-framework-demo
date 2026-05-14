#!/usr/bin/env bash
# Emergency rollback: route 100% of traffic back to V1.
# V2 revision is left deployed (deactivated) for forensic inspection.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

echo "=== ROLLBACK: 100% traffic -> $APP_NAME--v1 ==="
az containerapp ingress traffic set \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --revision-weight "$APP_NAME--v1=100" "$APP_NAME--v2=0" \
    -o none

echo "Deactivating V2 revision so it stops serving even if traffic is misrouted..."
az containerapp revision deactivate \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --revision "$APP_NAME--v2" \
    -o none || true

echo
echo "=== Rollback complete. Current weights: ==="
az containerapp ingress traffic show \
    --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" -o table
