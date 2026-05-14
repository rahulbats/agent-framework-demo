# Deploy the automated kill-switch infrastructure (PowerShell).
#
#   1. Logic App workflow (logic_app.bicep)            -- the kill agent
#   2. Action Group with LogicApp receiver             -- routes alerts
#   3. Scheduled-query alert rule (alert_rule.bicep)   -- the trigger
#   4. RBAC: grant Logic App MI "Azure AI User" on Foundry project
#
# Reuses the existing App Insights component and resource group.
# Run from this directory.

$ErrorActionPreference = 'Stop'

# ---- knobs ------------------------------------------------------------------
$RG               = 'agent-framework-demo'
$LOC              = 'eastus2'
$LOGIC_APP_NAME   = 'kill-switch-logicapp'
$ACTION_GROUP     = 'kill-switch-ag'
$ALERT_NAME       = 'agent-cost-threshold'
$APPI_NAME        = 'agent-framework-appi'
$FOUNDRY_ACCOUNT  = 'rahul-agent-framework-demo'
$FOUNDRY_PROJECT  = 'rahul-agent-framework-project'
$FOUNDRY_HOST     = "$FOUNDRY_ACCOUNT.services.ai.azure.com"
$FOUNDRY_PROJECT_PATH = "/api/projects/$FOUNDRY_PROJECT"
$THRESHOLD_USD    = 10
# ----------------------------------------------------------------------------

Write-Host "==> 1/4  Deploying Logic App workflow..." -ForegroundColor Cyan
$logicAppDeploy = az deployment group create `
    -g $RG -n kill-switch-logicapp `
    -f logic_app.bicep `
    -p logicAppName=$LOGIC_APP_NAME `
       foundryHost=$FOUNDRY_HOST `
       foundryProjectPath=$FOUNDRY_PROJECT_PATH `
    --query 'properties.outputs' -o json | ConvertFrom-Json

$LOGIC_APP_ID = $logicAppDeploy.logicAppId.value
$LOGIC_APP_PRINCIPAL = $logicAppDeploy.principalId.value
Write-Host "    logicApp     = $LOGIC_APP_ID"
Write-Host "    principalId  = $LOGIC_APP_PRINCIPAL"

Write-Host "==> 2/4  Granting Logic App MI 'Azure AI User' on Foundry project..." -ForegroundColor Cyan
$FOUNDRY_PROJECT_ID = az cognitiveservices account show `
    -g $RG -n $FOUNDRY_ACCOUNT --query id -o tsv
$FOUNDRY_PROJECT_SCOPE = "$FOUNDRY_PROJECT_ID/projects/$FOUNDRY_PROJECT"
az role assignment create `
    --assignee-object-id $LOGIC_APP_PRINCIPAL `
    --assignee-principal-type ServicePrincipal `
    --role 'Azure AI User' `
    --scope $FOUNDRY_PROJECT_SCOPE 2>&1 | Out-Null
Write-Host "    role assignment created (or already existed)"

Write-Host "==> 3/4  Creating action group with Logic App receiver..." -ForegroundColor Cyan
# Logic App receivers need the trigger callback URL (with SAS).
$CALLBACK_URL = az rest --method POST `
    --uri "https://management.azure.com$LOGIC_APP_ID/triggers/manual/listCallbackUrl?api-version=2016-06-01" `
    --query value -o tsv
Write-Host "    callbackUrl  = $($CALLBACK_URL.Substring(0, 80))..."

# Deploy via Bicep so the callback URL with '&' chars passes through cleanly.
# Use a parameters file to avoid cmd.exe interpreting '&' in the URL.
$agParams = @{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = @{
        actionGroupName = @{ value = $ACTION_GROUP }
        logicAppId      = @{ value = $LOGIC_APP_ID }
        callbackUrl     = @{ value = $CALLBACK_URL }
    }
} | ConvertTo-Json -Depth 5
$agParams | Set-Content .ag.params.json -NoNewline
$agDeploy = az deployment group create `
    -g $RG -n kill-switch-ag `
    -f action_group.bicep `
    --parameters '@.ag.params.json' `
    --query 'properties.outputs' -o json | ConvertFrom-Json
$ACTION_GROUP_ID = $agDeploy.actionGroupId.value
Write-Host "    actionGroup  = $ACTION_GROUP_ID"


Write-Host "==> 4/4  Creating scheduled-query alert rule..." -ForegroundColor Cyan
$APPI_ID = az monitor app-insights component show -g $RG -a $APPI_NAME --query id -o tsv
az deployment group create `
    -g $RG -n kill-switch-alert `
    -f alert_rule.bicep `
    -p alertName=$ALERT_NAME `
       appInsightsId=$APPI_ID `
       actionGroupId=$ACTION_GROUP_ID `
       thresholdUsd=$THRESHOLD_USD `
    -o none

Write-Host ""
Write-Host "==> DONE" -ForegroundColor Green
Write-Host "    Logic App callback URL (also written to .callback_url):"
Write-Host "    $CALLBACK_URL"
$CALLBACK_URL | Set-Content .callback_url -NoNewline
Write-Host ""
Write-Host "Test it by running:"
Write-Host "    python ..\demo_kill_switch.py"
Write-Host "Scenario C will POST a fake alert payload at the URL above and "
Write-Host "watch the Logic App kill the throwaway version."
