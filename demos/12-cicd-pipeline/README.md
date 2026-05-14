# Demo 12: CI/CD Pipeline (Foundry-managed agent)

Azure DevOps pipeline for a **hosted Azure AI Foundry agent** — versioned in
the Foundry project, not packaged as a container.

## Question Answered

> "We use Azure DevOps. We need a pipeline that publishes a new version of our
> Foundry agent, runs evaluation, gates on quality, and flips production."

## Why this looks different from a "normal" container pipeline

A Foundry-managed agent is a **definition** (instructions + model deployment +
tools + knowledge) stored inside your Foundry project. Each save creates a
new **version** (`agents.create_version`). There is **no Docker image, no
ACR, no Container Apps revision, no traffic-weight knob**.

| Concern | Self-hosted agent (Container Apps / AKS) | Foundry-managed agent (this pipeline) |
|---|---|---|
| Build artifact | Docker image | Agent version (instructions + tools) |
| Registry | ACR | The Foundry project itself |
| Deploy unit | Container revision | `agents.create_version()` call |
| Blue/green | Traffic-split between revisions | Two versions coexist; app reads version pointer |
| Promote | `az containerapp ingress traffic` | Flip `AGENT_VERSION` in App Configuration |
| Rollback | Shift traffic back | Set `AGENT_VERSION` back to previous |
| Scale | You size the container | Foundry handles it |

## Pipeline stages

```
Lint -> Test -> Publish (new version) -> Evaluate -> Promote -> Smoke
                       │                     │           │         │
              agents.create_version    azure-ai-eval   App      Live call
              in the Foundry project    + threshold   Config    (auto-rollback
                                          gate        flip       on failure)
```

## Repo layout this pipeline expects

```
agents/insurance-submission-agent/
  instructions.md          # system prompt, version-controlled
  tools.yaml               # MCP/function tool refs
  eval/dataset.jsonl       # eval queries (+ optional ground_truth)
scripts/
  validate_agent_definition.py
  publish_agent_version.py     # calls AIProjectClient.agents.create_version
  evaluate_agent_version.py    # uses azure-ai-evaluation, target = FoundryAgent
  smoke_test_agent.py
```

The Python helper scripts are thin wrappers over `azure-ai-projects` and
`azure-ai-evaluation` — the same SDKs Lab 10's portal flow uses.

## How "promotion" works (no traffic shifting)

1. Pipeline calls `agents.create_version()` → returns e.g. `v15`.
2. Evaluation runs against **that specific version** (`agent_version=15`).
3. If it passes the gate, pipeline writes `AGENT_VERSION=15` to **Azure App
   Configuration**.
4. Your application reads `AGENT_VERSION` at startup (or on a refresh signal)
   and invokes `FoundryAgent(agent_name=..., agent_version=...)` against that
   version.
5. Old versions stay in the project — rollback is just setting the App Config
   key back to `14`.

This is the canonical pattern because Foundry doesn't expose
percentage-based traffic splits for hosted agents the way Container Apps does.
If you need canarying, do it at the **caller** layer (e.g. 10 % of requests
read `AGENT_VERSION_CANARY`).

## One-time setup

1. **Service connection** `Foundry-Azure-Connection` (federated identity,
   scoped to the resource group containing the Foundry project) with:
   - **Azure AI User** on the project (to create versions)
   - **App Configuration Data Owner** on your App Config store
2. **Variable group** `foundry-agent` containing:
   - `FOUNDRY_PROJECT_ENDPOINT`
   - `AGENT_NAME` (e.g. `insurance-submission-agent`)
   - `MODEL_DEPLOYMENT` (e.g. `gpt-4o`)
   - `APP_CONFIG_NAME`, `PROD_VERSION_VAR_NAME` (e.g. `AGENT_VERSION`)
   - `EVAL_THRESHOLD` (e.g. `0.70`)
3. **Environment** `production` with required reviewers (the approval gate
   sits between Evaluate and Promote).

## Files

- [azure-pipelines.yml](azure-pipelines.yml) — pipeline definition
- README.md — this file

## When you actually DO want a container

If your agent needs custom Python tools that can't run as Foundry function
tools (heavy native deps, private VNet calls, etc.), host the **tool layer**
in Container Apps and keep the agent itself in Foundry. The pipeline above
still publishes the agent version; you'd add a parallel stage that builds &
pushes the tool container and updates its image tag — that's the only piece
that needs ACR + traffic shifting.
