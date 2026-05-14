# Demo 05 — Observability for Foundry Agents

End-to-end OpenTelemetry tracing for a real Foundry agent. We re-run the
Lab 1 insurance-submission agent (V1 + V2) and capture every model call,
every tool dispatch, and every local function execution as OTel spans
that flow into Azure Application Insights.

## What you get

- **Server-side traces** from Foundry — `invoke_agent`, `process_thread_run`,
  `create_message`, `submit_tool_outputs`, with `gen_ai.*` semantic-convention
  attributes (model, input/output tokens, agent_id, thread_id, run_id).
- **Client-side traces** from this script — a `demo.turn` parent span per
  user prompt, plus `tool.<name>` spans around each local tool execution.
- **Cost / latency / A-B analytics** via the KQL queries in
  [kql_queries.md](kql_queries.md).

All telemetry lands in the `dependencies` table of App Insights.

## Architecture

```
                 ┌──────────────────┐
 user prompt ──▶│  demo.turn span  │  (manual)
                 └────────┬─────────┘
                          │
                          ▼
   AIProjectInstrumentor  →  invoke_agent / process_thread_run / ... (Foundry SDK)
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
  tool.<name> span (manual)   gen_ai.* server-side spans
              │                        │
              └──────────┬─────────────┘
                         ▼
        azure-monitor-opentelemetry exporter
                         ▼
                Azure Application Insights
```

## Prerequisites

1. Lab 1 working (`demos/01-local-agent-dev/.env` has `FOUNDRY_ENDPOINT`,
   `AZURE_OPENAI_DEPLOYMENT`).
2. `az login` (the script uses `AzureCliCredential`).
3. Python deps:
   ```powershell
   pip install azure-ai-projects azure-identity python-dotenv rich `
               azure-monitor-opentelemetry opentelemetry-api opentelemetry-sdk
   ```

## One-time infrastructure setup

The App Insights workspace `agent-framework-appi` is already created and
linked to the Foundry project. To recreate from scratch:

```powershell
$RG  = "agent-framework-demo"
$LOC = "eastus2"
$WS  = (az monitor log-analytics workspace list -g $RG --query "[0].id" -o tsv)

# 1. Create App Insights (workspace-based)
az monitor app-insights component create `
  -g $RG -l $LOC -a agent-framework-appi --workspace $WS

# 2. Grab the connection string
$conn = az monitor app-insights component show `
  -g $RG -a agent-framework-appi --query connectionString -o tsv
$conn | Set-Content demos\05-observability\.env -NoNewline
"APPLICATIONINSIGHTS_CONNECTION_STRING=$conn" | Set-Content demos\05-observability\.env

# 3. Link App Insights to the Foundry project (so Foundry-side traces flow too)
$resId  = az monitor app-insights component show -g $RG -a agent-framework-appi --query id -o tsv
$body = @{
  properties = @{
    category   = "AppInsights"
    target     = $resId
    authType   = "ApiKey"
    isSharedToAll = $true
    metadata   = @{ ApiType = "Azure" }
    credentials = @{ key = $conn }
  }
} | ConvertTo-Json -Depth 5

$endpoint = "<your-foundry-endpoint>"  # https://<resource>.services.ai.azure.com/api/projects/<project>
az rest --method PUT `
  --url "$endpoint/connections/agent-framework-appi?api-version=2025-05-01" `
  --body $body
```

## Run

```powershell
cd demos\05-observability
python demo_observability.py
```

The script:
1. Sets `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` (required by the SDK).
2. Calls `configure_azure_monitor()` with the App Insights connection string.
3. Calls `AIProjectInstrumentor().instrument(enable_content_recording=True)`.
4. Creates two versions of `insurance-submission-agent` (V1 analyst, V2 senior underwriter).
5. Runs 4 user prompts, each randomly assigned to V1 or V2.
6. Wraps each turn in a `demo.turn` span and each tool execution in a `tool.<name>` span.

## Inspect traces

Wait ~1–2 minutes for ingestion, then open App Insights:

```powershell
az monitor app-insights component show -g agent-framework-demo `
   -a agent-framework-appi --query "[id]" -o tsv
```

Paste the resource ID into the Azure portal URL bar, then go to **Logs** and run:

```kusto
dependencies
| where timestamp > ago(15m)
| where name startswith "invoke_agent" or name == "demo.turn" or name startswith "tool."
| project timestamp, name, duration, success, operation_Id
| order by timestamp desc
```

Then explore the queries in [kql_queries.md](kql_queries.md) — token usage, cost
per turn, V1 vs V2, tool latency, end-to-end timeline for one operation.

## Quick CLI verification

```powershell
az monitor app-insights query --app agent-framework-appi -g agent-framework-demo `
  --analytics-query "dependencies | where timestamp > ago(10m) | where name startswith 'invoke_agent' or name == 'demo.turn' or name startswith 'tool.' | summarize count() by name | order by count_ desc" `
  -o table
```

## Files

| File                        | Purpose                                          |
|-----------------------------|--------------------------------------------------|
| `demo_observability.py`     | Runs the agent, emits OTel traces                |
| `kql_queries.md`            | KQL queries against the `dependencies` table     |
| `.env`                      | `APPLICATIONINSIGHTS_CONNECTION_STRING`          |

## Troubleshooting

- **No traces appear** — confirm `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`
  is set *before* `from azure.ai.projects.telemetry import AIProjectInstrumentor`.
  In this script it's the first thing after `import os`.
- **Only manual spans, no `gen_ai.*` Foundry spans** — the AppInsights connection
  isn't linked to the Foundry project. Re-run the `az rest PUT` step above.
- **`LogData` ImportError on startup** — version skew between the Azure Monitor
  exporter and `opentelemetry-sdk`. Run
  `pip install -U azure-monitor-opentelemetry opentelemetry-sdk opentelemetry-api`.
