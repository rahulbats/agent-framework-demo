# Demo 08 — MCP Server + APIM-governed Agent Loop

Stand up a real **Model Context Protocol** server and have a model call its tools
through the APIM gateway from Lab 7. End-to-end agent loop, all hops logged.

## What this proves

1. **Real MCP** — `mcp_server.py` uses the official `mcp` Python SDK (`FastMCP`)
   with the **streamable-http** transport (the current MCP standard). Same
   protocol Foundry / Claude Desktop / Cursor speak.
2. **APIM in front of the model** — the agent loop in `demo_mcp_client.py` uses
   `AzureOpenAI` pointed at `APIM_GATEWAY_URL` (Lab 7's gateway). Every model
   call is policy-governed.
3. **Composability** — MCP tools and APIM-governed models are independent
   layers. Swap the model, swap the tool server, the loop is unchanged.

```
┌────────────────────┐     tool calls      ┌────────────────────┐
│  demo_mcp_client   │ ──────────────────► │  mcp_server.py     │
│  (agent loop)      │ ◄────────────────── │  FastMCP / HTTP    │
│                    │     tool results    │  port 8081 /mcp    │
│                    │                     └────────────────────┘
│                    │
│   chat completions │     APIM policies (Lab 7)
│                    │ ──────────────────► [rahul-ai-gateway]
│                    │ ◄────────────────── [token-limit, metric,
└────────────────────┘                      semantic-cache, MI auth]
                                                    │
                                                    ▼
                                            Azure OpenAI gpt-4o
```

## Files

| File | Purpose |
|---|---|
| [mcp_server.py](mcp_server.py) | FastMCP server, three tools (`get_policy`, `search_guidelines`, `get_loss_runs`) |
| [demo_mcp_client.py](demo_mcp_client.py) | Scenario A: protocol probe. Scenario B: agent loop using APIM-served gpt-4o + MCP tools. |
| [demo_mcp_registry.py](demo_mcp_registry.py) | Scenario C: discover MCP URL from Azure API Center, then run the same agent loop. |
| [automation/api_center.bicep](automation/api_center.bicep) | Provisions Azure API Center and registers the MCP server in its catalog. |

## Prereqs

- Python deps: `pip install "mcp[cli]>=1.2.0" openai python-dotenv rich azure-identity httpx`
- Lab 7 deployed: `APIM_GATEWAY_URL`, `APIM_SUBSCRIPTION_KEY`, `AOAI_DEPLOYMENT`
  in [`demos/01-local-agent-dev/.env`](../01-local-agent-dev/.env).

## Run

Terminal 1 — start the MCP server:

```powershell
cd demos\08-mcp-server
python mcp_server.py
# -> http://0.0.0.0:8081/mcp
```

Terminal 2 — run the demo:

```powershell
cd demos\08-mcp-server
python demo_mcp_client.py
```

Expected output: scenario A lists/calls each tool; scenario B shows the model
choosing tools (`hop 1 model -> tool get_policy(...)` etc.), the tool
returning, and a final assistant answer.

## Optional — verify with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# Connect to: http://localhost:8081/mcp   (transport: streamable-http)
```

## How this connects to other labs

- **Lab 7 (APIM Gateway)** — same gateway URL & key. The agent loop here is
  exactly what an agent runtime (Foundry, Agent Framework SDK, etc.) does
  internally when it consumes MCP tools and calls a governed model.
- **Lab 9 (Multi-agent)** — multiple agents can share one MCP server (one
  source of truth for "look up a policy") and one APIM gateway (one place
  for token limits and metrics).

## Scenario C — MCP discovery via Azure API Center

Real agents shouldn't hard-code MCP URLs. Azure API Center is the
**registry**: APIs of `kind: 'mcp'` are listed in the catalog, and each
deployment record carries the runtime URL.

### Provision the registry

```powershell
az deployment group create `
  --resource-group agent-framework-demo `
  --template-file demos\08-mcp-server\automation\api_center.bicep `
  --name api-center-lab8
```

This creates `rahul-api-center` (Free SKU, eastus) with workspace `default`,
environment `local-dev`, and registers `internal-apis` (`kind: mcp`) with a
deployment pointing at `http://localhost:8081/mcp`. To register a publicly
reachable URL instead (devtunnel, ngrok, App Service), pass
`--parameters mcpRuntimeUri=https://...`.

### Verify the catalog

```powershell
az rest --method GET `
  --uri "https://management.azure.com/subscriptions/<SUB>/resourceGroups/agent-framework-demo/providers/Microsoft.ApiCenter/services/rahul-api-center/workspaces/default/apis?api-version=2024-06-01-preview" `
  --query "value[].{name:name,kind:properties.kind,title:properties.title}" -o table
```

```
Name              Kind    Title
----------------  ------  -------------------
internal-apis     mcp     Internal APIs (MCP)
```

### Run the registry-driven agent

Terminal 1 keeps the MCP server running. Terminal 2:

```powershell
cd demos\08-mcp-server
python demo_mcp_registry.py
```

The script lists APIs from API Center, filters to `kind == 'mcp'`, picks
`internal-apis`, reads its active deployment for the runtime URI, opens an
MCP session at that URI, and runs the same APIM-governed agent loop as
Scenario B. No URL is hard-coded in the script.

## Forward-looking patterns (not run in this lab)

### REST → MCP via APIM

APIM can publish an existing OpenAPI-described REST API as an MCP server, no
code changes required:

```bash
az apim api import \
    --resource-group agent-framework-demo --service-name rahul-ai-gateway \
    --api-id omega-api --path /omega \
    --specification-format OpenApiJson --specification-path ./omega-openapi.json

az apim api export \
    --resource-group agent-framework-demo --service-name rahul-ai-gateway \
    --api-id omega-api --format mcp
# -> https://rahul-ai-gateway.azure-api.net/mcp/omega-api
```

## Cleanup

Nothing to clean — the MCP server is a local Python process; ⌃C in terminal 1.
