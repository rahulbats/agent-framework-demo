# Demo 07 — LLM Gateway with Azure API Management

Put **Azure API Management (APIM)** in front of Azure OpenAI so every agent call
is governed by policy instead of by raw SDK calls. The agent never sees the
AOAI key — it only holds an APIM subscription key.

## What APIM is doing here

| Policy                              | What it gives you                                                           |
|-------------------------------------|-----------------------------------------------------------------------------|
| `authentication-managed-identity`   | APIM signs the AOAI request with its own system-assigned MI. No AOAI key.   |
| `llm-token-limit`                   | Per-subscription-key TPM cap. Returns 429 + `retry-after` when exhausted.   |
| `llm-emit-token-metric`             | Emits `TokenMetric` to App Insights tagged with `Agent` + `Session` headers.|
| `llm-semantic-cache-lookup`/`store` | Cache hits on semantically-similar prompts. *(Requires external Redis.)*    |

Result: the policy XML is the single place you tune cost controls, attribution,
and routing. Application code stops needing to know any of it.

## Architecture

```
agent
  | x-agent-name, x-session-id, Ocp-Apim-Subscription-Key
  v
APIM (BasicV2, system-assigned MI)
  |- authentication-managed-identity --> AOAI (Cognitive Services OpenAI User)
  |- llm-token-limit                  --> 429 if subscription budget empty
  |- llm-emit-token-metric            --> App Insights customMetrics
  '-> AOAI gpt-4o deployment
```

## Files

| Path                                          | Purpose                                                  |
|-----------------------------------------------|----------------------------------------------------------|
| `automation/apim_service.bicep`               | Provisions the APIM service (BasicV2, system MI).        |
| `automation/apim_ai_gateway.bicep`            | Backends, API, product, subscription, and policy XML.    |
| `demo_llm_gateway.py`                         | Live demo against the deployed gateway.                  |

## Prereqs

- An AOAI account with a `gpt-4o` deployment.
- Azure CLI logged in.
- `pip install -r demos/01-local-agent-dev/requirements.txt`
- `.env` at `demos/01-local-agent-dev/.env` containing:
  ```
  APIM_GATEWAY_URL=https://<your-apim>.azure-api.net
  APIM_SUBSCRIPTION_KEY=<demo-key primary key>
  AOAI_DEPLOYMENT=gpt-4o
  ```

## Provision

```powershell
cd demos/07-llm-gateway/automation

# 1. APIM service (BasicV2 — needed for the llm-* policies; Consumption SKU does NOT support them)
az deployment group create -g agent-framework-demo -n apim-service `
    -f apim_service.bicep

# 2. Grant APIM's MI the AOAI data-plane role
$AOAI_ID = az cognitiveservices account show -g agent-framework-demo -n <aoai-account> --query id -o tsv
$MI = az apim show -g agent-framework-demo -n rahul-ai-gateway --query identity.principalId -o tsv
az role assignment create --assignee-object-id $MI --assignee-principal-type ServicePrincipal `
    --role "Cognitive Services OpenAI User" --scope $AOAI_ID

# 3. Wire backend, API, policies, product, subscription
az deployment group create -g agent-framework-demo -n apim-ai-gateway `
    -f apim_ai_gateway.bicep `
    -p aoaiEndpoint="https://<aoai-account>.cognitiveservices.azure.com/"

# 4. Grab the subscription key
az rest --method POST `
    --uri "https://management.azure.com/subscriptions/$((az account show --query id -o tsv))/resourceGroups/agent-framework-demo/providers/Microsoft.ApiManagement/service/rahul-ai-gateway/subscriptions/demo-key/listSecrets?api-version=2023-05-01-preview" `
    --query primaryKey -o tsv
```

## Run the demo

```powershell
python demos/07-llm-gateway/demo_llm_gateway.py
```

Four scenarios:

1. **A. Governed call** — three calls succeed; response carries `x-tokens-consumed` and `x-ratelimit-remaining-tokens` headers from the policy.
2. **B. Auth at the gateway** — call with a bogus key gets `401` from APIM. AOAI is never reached.
3. **C. Token-limit kicks in** — large prompts in a loop drain the 1000-TPM bucket; APIM short-circuits with `429 Too Many Requests` and `retry-after`.
4. **D. Per-agent attribution** — query `customMetrics` in Application Insights for `TokenMetric` grouped by `Agent` + `Session` dimensions.

Sample run output (real numbers from the deployed gateway):

```
A. Managed identity + token metering
  call 1  status=200  77 tokens   923 remaining
  call 2  status=200  77 tokens   859 remaining
  call 3  status=200  77 tokens   831 remaining

B. Auth at the gateway
  status: 401 — Access denied due to invalid subscription key.

C. Token-limit kicks in
  call 1  status=200  consumed=452  remaining=450
  call 2  status=200  consumed=452  remaining=110
  call 3  status=200  consumed=452  remaining=  0
  call 4  status=200  consumed=452  remaining=  0
  call 5  status=429  retry-after=16s  -> backend was not hit
```

## Notes & limitations

- **Semantic cache** is wired in policy but will MISS until you attach an
  external Redis cache (`Microsoft.Cache/Redis` + APIM `caches` resource).
  BasicV2 does not include an internal cache. Premium and Developer SKUs do.
- **App Insights metric** (`TokenMetric`) requires an APIM `loggers` resource
  pointed at App Insights and a diagnostic setting on the API. Add those if
  you want Scenario D's KQL to return rows.
- Consumption SKU **cannot** host `llm-*` or `azure-openai-*` policies. If you
  see `ValidationError ... Policy is not allowed in 'Consumption' sku`, you're
  on the wrong tier. This lab uses BasicV2.

## Cleanup

```powershell
az apim delete -g agent-framework-demo -n rahul-ai-gateway --yes --no-wait
# Optional, frees the name immediately for redeploy:
az apim deletedservice purge --service-name rahul-ai-gateway --location eastus2
```
