# Demo 04: Agent Identity

A Foundry agent that uses a **dedicated Service Principal** (its "Agent
Identity") to call a real Azure resource (Blob Storage). The agent never
runs as you — every Storage call is audited as the Agent Identity.

## What This Demonstrates

- Creating a least-privilege Service Principal as the agent's identity
- Granting it just one role (`Storage Blob Data Reader`) on one storage
  account — no Contributor, no broad scopes
- An agent tool that uses `ClientSecretCredential` (locally) to act as that
  SP when calling Azure Storage
- The token's `oid`/`appid` claims printed at runtime so you can see the
  call really is authenticated as the SP

## Why Service Principal (and not Entra Agent Identity Blueprints)?

Entra **Agent Identity** is a newer, stricter object model with
per-instance identities and a 2-step token exchange. It's powerful but
heavy. For most teams, a plain Service Principal (or, in production, a
Managed Identity) is the pragmatic answer:

| | Service Principal | Entra Agent Identity Blueprint |
|---|---|---|
| Objects to create | 1 SP | Blueprint + BlueprintPrincipal + AgentIdentity |
| Credential | secret OR cert OR FIC | secret/MI on Blueprint, none on AgentIdentity |
| Token exchange in code | `cred.get_token(scope)` | 2-step `fmi_path` exchange |
| Per-instance audit | One SP per agent type | Distinct SP per agent instance |
| Setup time | Minutes | Hours + admin consent |

This lab uses the SP path. If you later need per-instance auditing
(e.g. one identity per tenant of a SaaS agent), graduate to Blueprints.

## Local vs Azure

The tool uses `ClientSecretCredential` because your laptop has no Managed
Identity. **In Azure, swap two lines:**

```diff
- from azure.identity import ClientSecretCredential
- agent_credential = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
+ from azure.identity import DefaultAzureCredential
+ agent_credential = DefaultAzureCredential()
```

Assign the same SP (or a User-Assigned MI with the same role grant) to
your Container App / AKS pod / App Service, and `DefaultAzureCredential`
picks it up automatically. **No other code change.**

## Prereqs

- `az login` with **Owner** (or Contributor + User Access Administrator)
  on the resource group you'll use (this lab assumes `agent-framework-demo`
  in `eastus2` — change the commands if yours is different).
- Lab 1 has been run at least once (this lab reuses its `FOUNDRY_ENDPOINT`).

## Setup (manual — copy/paste these commands)

The setup is six `az` calls and one `.env` write. Each step is independent
so you can re-run individual ones safely.

### 1. Create the storage account

```pwsh
$RG  = "agent-framework-demo"
$LOC = "eastus2"
# storage account names must be globally unique, lowercase, no dashes
$SA  = "agentdemo" + (-join ((1..8) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) }))

az storage account create `
  -n $SA -g $RG -l $LOC `
  --sku Standard_LRS --kind StorageV2 `
  --allow-blob-public-access false
```

### 2. Grant *yourself* Storage Blob Data Contributor (so you can upload)

```pwsh
$saId  = az storage account show -n $SA -g $RG --query id -o tsv
$myOid = az ad signed-in-user show --query id -o tsv

az role assignment create `
  --assignee-object-id $myOid `
  --assignee-principal-type User `
  --role "Storage Blob Data Contributor" `
  --scope $saId
# wait ~30-60 seconds for propagation before step 3
```

### 3. Create the container and upload the sample policy doc

```pwsh
az storage container create `
  --account-name $SA -n policy-docs `
  --auth-mode login --public-access off

@"
COMMERCIAL PROPERTY POLICY - SUMMARY (sample doc)

Insured:           Acme Logistics, LLC
Policy number:     CP-2026-441872
Effective:         2026-01-15 to 2027-01-15
Limits:            `$10,000,000 building / `$2,500,000 contents
Deductible:        `$25,000 per occurrence
Covered locations: 1 (Atlanta, GA - 250,000 sqft warehouse)
Notable exclusions: flood, earthquake, mold

Loss history (last 24 mo):
  - 2024-08: minor water damage, paid `$42,150
  - 2025-03: theft, paid `$18,400

Underwriter notes: TIV exceeds primary program cap; refer to facultative
reinsurance for layer above `$5M.
"@ | Out-File -Encoding utf8 .\policy-document.txt

az storage blob upload `
  --account-name $SA -c policy-docs `
  -n policy-document.txt -f .\policy-document.txt `
  --auth-mode login --overwrite true
```

### 4. Create the Service Principal (the Agent Identity)

```pwsh
# IMPORTANT: this command prints `password` ONCE. Copy it now.
$sp = az ad sp create-for-rbac --name agent-framework-demo-sp --years 1 -o json | ConvertFrom-Json
$sp   # shows appId, password, tenant — write these down
```

`create-for-rbac` adds a default `Contributor` role at the subscription
scope. Strip it so the SP follows least-privilege:

```pwsh
$contrib = az role assignment list --assignee $sp.appId `
  --query "[?roleDefinitionName=='Contributor'].id" -o tsv
if ($contrib) { az role assignment delete --ids $contrib }
```

### 5. Grant the SP just `Storage Blob Data Reader` on the storage account

```pwsh
az role assignment create `
  --assignee $sp.appId `
  --role "Storage Blob Data Reader" `
  --scope $saId
# wait ~30-60 seconds for propagation before step 7
```

### 6. Write `.env`

```pwsh
@"
AZURE_TENANT_ID=$($sp.tenant)
AZURE_CLIENT_ID=$($sp.appId)
AZURE_CLIENT_SECRET=$($sp.password)
STORAGE_ACCOUNT=$SA
STORAGE_CONTAINER=policy-docs
STORAGE_BLOB=policy-document.txt
"@ | Out-File -Encoding utf8 .\.env
```

> The client secret is shown only once by `create-for-rbac`. If you lose
> it, rotate with:
> `az ad sp credential reset --id <appId> --years 1`

`FOUNDRY_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT` are inherited from
`../01-local-agent-dev/.env`, so you don't need to repeat them.

### 7. Run the demo

```pwsh
python demo_agent_identity.py
```

## Expected Output

```
Demo 04 — Agent Identity in action
Foundry control-plane runs as: you@yourtenant.onmicrosoft.com
Agent tool calls run as:        Service Principal 8f3a...
Storage account:                agentdemo7c2f...

User: Please summarize the policy in policy-document.txt.

  -> read_policy_document({"name":"policy-document.txt"})
  Storage token claims: {
    "appid": "8f3a...",          <-- the Agent Identity SP, NOT you
    "oid":   "<sp-object-id>",
    "aud":   "https://storage.azure.com"
  }

Agent: This is a Commercial Property policy for Acme Logistics, LLC
       (CP-2026-441872). Limits: $10M building / $2.5M contents,
       $25K deductible. Notable exclusions: flood, earthquake, mold.
       Two recent claims totaling ~$60K. TIV exceeds the primary cap —
       refer for facultative reinsurance above $5M.

Identity audit trail
  Foundry: create agent + conversation       -> your `az login` user
  Tool: read_policy_document -> Storage      -> Service Principal 8f3a...
```

## Cleanup

```powershell
# Read IDs from .env and tear down everything created.
$env:AZURE_CLIENT_ID    = (Select-String '^AZURE_CLIENT_ID=' .env | ForEach-Object { ($_ -split '=',2)[1] })
$env:STORAGE_ACCOUNT    = (Select-String '^STORAGE_ACCOUNT=' .env | ForEach-Object { ($_ -split '=',2)[1] })

az ad sp delete --id $env:AZURE_CLIENT_ID
az storage account delete -n $env:STORAGE_ACCOUNT -g agent-framework-demo --yes
Remove-Item .env, policy-document.txt -ErrorAction SilentlyContinue
```

## Why This Matters

In production multi-agent systems, every agent action should be traceable
to a unique principal. Putting a single shared client secret in agent code
(or in env vars accessible to the agent) collapses that audit story and
violates least-privilege. Giving the agent its own dedicated identity
(SP locally, MI in Azure) with only the roles it actually needs is the
baseline for safe agent deployment.
