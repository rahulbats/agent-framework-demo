# Demo 02 — Containerized Agent on Azure Container Apps

**Self-hosted** insurance-submission agent: the agent loop runs in a Python process
inside a container. We package it, push to ACR, deploy to Azure Container Apps,
and demonstrate a real **blue/green canary rollout** using ACA's revision-based
traffic splitting.

> **Why ACA, not Foundry Agent Service?** Foundry Agent Service runs
> *PromptAgentDefinitions* — declarative agents (instructions + tools + model)
> that it executes server-side. It does **not** host arbitrary container images.
> When you want to keep the agent loop in your own process (for portability,
> custom orchestration, or because you're not on Foundry yet), Container Apps is
> the right Azure target. See the Lab 1 / Lab 5 path for the Foundry-hosted
> alternative.

## What this lab demonstrates

| Capability | How |
|---|---|
| Build & push image | `az acr build` straight to ACR (no local Docker required) |
| Authn to Azure OpenAI | User-assigned managed identity + `Cognitive Services OpenAI User` role |
| Authn to ACR | Same UAMI + `AcrPull` role (no admin user, no passwords) |
| Versioning | ACA **revisions** — `--revision-suffix v1`, `--revision-suffix v2` |
| Blue/green canary | `az containerapp ingress traffic set --revision-weight v1=90 v2=10` |
| Promote / rollback | One CLI command, no redeploy |
| Observability | Optional `APPLICATIONINSIGHTS_CONNECTION_STRING` env var → Lab 5 schema |

## Project layout

```
02-containerized-agent/
├── Dockerfile              # python:3.11-slim, exposes port 8080
├── requirements.txt
├── app/
│   ├── agent.py            # HTTP server + agent loop (calls AOAI directly)
│   ├── config.py           # env-var-driven config
│   └── data/               # sample submission documents (same as Lab 1)
├── deploy/
│   ├── .env                # shared deploy config (RG, ACR, UAMI, AOAI...)
│   ├── deploy-v1.sh        # build + create app, 100% on v1
│   ├── deploy-v2.sh        # build + add v2 revision, 90/10 canary
│   ├── promote-v2.sh       # 100% to v2
│   └── rollback-v1.sh      # 100% back to v1, deactivate v2
└── README.md
```

## Prerequisites (one-time)

The setup below provisions the user-assigned identity and grants the two
roles it needs (ACR pull + AOAI access). Skip if `submission-agent-uami` and
the role assignments already exist.

```powershell
$RG    = "agent-framework-demo"
$LOC   = "eastus2"
$ACR   = "rahulagentfwacr"            # existing ACR
$ACAEN = "agent-framework-env"        # existing Container Apps env
$AOAI  = "rahul-agent-framework-demo" # existing AOAI / Foundry account

# 1. Create the user-assigned managed identity
az identity create -g $RG -n submission-agent-uami -l $LOC -o table
$principalId = az identity show -g $RG -n submission-agent-uami --query principalId -o tsv

# 2. Let it pull from ACR
$acrId = az acr show -n $ACR -g $RG --query id -o tsv
az role assignment create `
  --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
  --role AcrPull --scope $acrId

# 3. Let it call Azure OpenAI
$aoaiId = az cognitiveservices account show -n $AOAI -g $RG --query id -o tsv
az role assignment create `
  --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services OpenAI User" --scope $aoaiId

# 4. Container Apps CLI extension (preview)
az extension add -n containerapp --upgrade
az provider register -n Microsoft.App
az provider register -n Microsoft.OperationalInsights

# 5. Update deploy/.env with the UAMI clientId + resource ID printed above
```

## Workflow

```bash
cd deploy

# 1. Initial deploy — V1 at 100%
bash deploy-v1.sh

# 2. Hit /invoke a few times, watch traces in App Insights (Lab 5 queries work)
FQDN=$(az containerapp show -g agent-framework-demo -n submission-agent \
        --query properties.configuration.ingress.fqdn -o tsv)
curl -s "https://$FQDN/health"
curl -s -X POST "https://$FQDN/invoke" -H 'content-type: application/json' \
     -d '{"input":"List the submission documents and tell me what is in the loss run."}'

# 3. Bump app/agent.py (e.g. change SYSTEM_PROMPT), then deploy V2 as canary
bash deploy-v2.sh        # V1=90 / V2=10

# 4a. Healthy? Promote V2 to 100%
bash promote-v2.sh

# 4b. Issues? Roll back to V1, deactivate V2
bash rollback-v1.sh
```

## How traffic splitting actually works

ACA supports **revisions** — immutable snapshots of the app's image + env vars.
With `--revisions-mode multiple`, several revisions can be active at once and
the ingress sends traffic to them in the configured weights.

```
                 ┌─── 90% ───▶ submission-agent--v1   (image :v1)
client ──https──▶│
                 └─── 10% ───▶ submission-agent--v2   (image :v2)   ← canary
```

Each `bash deploy-v*.sh` call:

1. `az acr build ... :vN` — builds the image inside ACR
2. `az containerapp update ... --revision-suffix vN` — adds revision `app--vN`
3. `az containerapp ingress traffic set --revision-weight ...` — splits traffic

**Rollback is instant** because V1 is still running — `traffic set` just changes
the weights. No image rebuild, no container restart.

## Inspect

```powershell
# Active revisions
az containerapp revision list -g agent-framework-demo -n submission-agent -o table

# Current traffic split
az containerapp ingress traffic show -g agent-framework-demo -n submission-agent -o table

# Logs
az containerapp logs show -g agent-framework-demo -n submission-agent --follow

# Per-revision metrics (CPU/mem/req)
az monitor metrics list \
  --resource $(az containerapp show -g agent-framework-demo -n submission-agent --query id -o tsv) \
  --metric Requests --interval PT1M -o table
```

## Local sanity check

```powershell
# Build locally
docker build -t submission-agent:local .

# Run with a static AOAI token (for dev — UAMI doesn't work outside Azure)
$token = az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv
docker run --rm -p 8080:8080 `
  -e AZURE_OPENAI_ENDPOINT=https://rahul-agent-framework-demo.cognitiveservices.azure.com/ `
  -e AZURE_OPENAI_DEPLOYMENT=gpt-4o `
  -e AZURE_OPENAI_TOKEN=$token `
  submission-agent:local

# In another shell
curl -s http://localhost:8080/health
curl -s -X POST http://localhost:8080/invoke -H 'content-type: application/json' `
     -d '{"input":"What submission documents are available?"}'
```

## Troubleshooting

- **`az containerapp` not found** — `az extension add -n containerapp --upgrade`
- **Image pull fails** — confirm the UAMI has `AcrPull` on the registry and is
  attached to the app (`az containerapp identity show -g ... -n ...`)
- **401 from Azure OpenAI** — confirm `Cognitive Services OpenAI User` role on
  the AOAI account, and that `AZURE_CLIENT_ID` is set to the **client (app) id**
  of the UAMI, not the principal id
- **Both revisions get 0% traffic** — `--revisions-mode multiple` must be set at
  create time. If not, run `az containerapp revision set-mode -m multiple ...`
