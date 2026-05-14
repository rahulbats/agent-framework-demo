# Demo 06: Kill Switch

Three real, runnable kill-switch scenarios against the live Foundry project from Labs 1 / 5.

## Question Answered

> "Each run is costing me $100, so I might invoke the kill switch on version two."

## What This Demonstrates

| # | Scenario | API used |
|---|----------|----------|
| **A** | Cancel an in-flight Foundry response (background mode) | `openai_client.responses.cancel(id)` |
| **B** | Manually delete a specific agent **version** | `project.agents.delete_version(name, version)` |
| **C** | **Automated** kill: App Insights cost alert → action group → Logic App → Foundry REST DELETE | Azure Monitor → Logic App MI → `DELETE /agents/{name}/versions/{ver}` |

## Architecture (scenario C)

```
 Lab 5 telemetry          Azure Monitor                 Logic App                Foundry
 ────────────────         ─────────────                 ─────────                 ───────
 dependencies      ──►    KQL alert rule        ──►    HTTP trigger      ──►    DELETE /agents/
 (gen_ai.usage.*)         agent-cost-threshold         (system MI)               {name}/versions/{ver}
                          window=15m, eval=5m           audience: ai.azure.com    api-version=v1
                          threshold = $10/15m           "Azure AI User" RBAC
                          dims: agent_name,
                                agent_version
```

The Logic App's system-assigned managed identity acquires a token for `https://ai.azure.com` and calls Foundry directly — **no custom code, no container, no Function**.

## Files

| File | Purpose |
|------|---------|
| `demo_kill_switch.py` | Runs A + B + C end-to-end |
| `automation/logic_app.bicep` | Logic App workflow: parses common alert schema, calls Foundry DELETE |
| `automation/action_group.bicep` | Action group with Logic App receiver (useCommonAlertSchema=true) |
| `automation/alert_rule.bicep` | Scheduled-query alert rule on the App Insights component |
| `automation/deploy.ps1` | Deploys all three + grants the Logic App MI "Azure AI User" on the Foundry project |

## Prereqs

- Lab 1 has been run (the agent `insurance-submission-agent` exists in the Foundry project)
- Lab 5 has been run (App Insights is connected and emitting `dependencies` rows)
- `az login` as a user with role-assignment rights on the Foundry account

## Run

```powershell
# 1. Deploy the automation infrastructure (Logic App + action group + alert rule).
cd automation
.\deploy.ps1

# 2. Run the demo. Scenario C reads .callback_url written by deploy.ps1
#    and POSTs an Azure Monitor common-alert-schema payload at the Logic App.
cd ..
python demo_kill_switch.py
```

Expected scenario C output:

```
-> POSTing alert payload at the Logic App trigger URL...
  Logic App responded 200: {"action":"killed","agent_name":"insurance-submission-agent","agent_version":"8","foundry_status":200}
  versions after Logic App ran: ['1', '2', '3', '4']
OK Logic App autonomously killed v8.
```

## How the alert finds the offending version

The KQL in `alert_rule.bicep` groups by `agent_name` + `agent_version`, so the alert payload contains those two as **dimensions**. The Logic App pulls them out and constructs the DELETE URL.

```kql
dependencies
| where timestamp > ago(15m)
| where customDimensions has "gen_ai.agent.name"
| extend agent_name    = tostring(customDimensions["gen_ai.agent.name"])
| extend agent_version = tostring(coalesce(customDimensions["gen_ai.agent.version"], "1"))
| extend total_tokens  = toint(customDimensions["gen_ai.usage.total_tokens"])
| extend cost_usd      = total_tokens * 10.0 / 1000000.0   // ~$10/1M blended
| summarize cost_usd = sum(cost_usd) by agent_name, agent_version
```

> The cost formula is intentionally rough. Replace with your model's actual input/output rates.

## Cleanup

```powershell
az deployment group delete -g agent-framework-demo -n kill-switch-alert
az deployment group delete -g agent-framework-demo -n kill-switch-ag
az deployment group delete -g agent-framework-demo -n kill-switch-logicapp
az monitor scheduled-query delete -g agent-framework-demo -n agent-cost-threshold -y
az monitor action-group delete   -g agent-framework-demo -n kill-switch-ag
az resource delete -g agent-framework-demo -n kill-switch-logicapp --resource-type Microsoft.Logic/workflows
```
