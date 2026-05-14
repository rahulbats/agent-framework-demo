# Demo 01: Foundry-Hosted Prompt Agent (GA)

**Phase 1** — Agent created and hosted on the **GA Microsoft Foundry Agent Service**.

## What This Demonstrates

- **Hosted prompt agent** — definition (model, instructions, tools) lives in Foundry and is **editable in the portal** under **Build → Agents**
- **Versioned agents** — `create_version` snapshots a new version on every change
- **Foundry Conversations** — multi-turn state managed server-side (no local message list)
- **Responses API** — `openai.responses.create(...)` with `agent_reference`
- **Custom function tools** — schema declared on the agent; your local Python executes them and submits the output back
- `DefaultAzureCredential` — no API keys

## Prerequisites

```bash
pip install -r requirements.txt
az login
```

`.env` must contain:

```
FOUNDRY_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AGENT_NAME=insurance-submission-agent     # optional
```

## Run

```bash
python main.py
```

The agent persists in your Foundry project after exit — open it in the portal to edit instructions, swap models, or inspect runs.

## Architecture

```
Local Machine                        Microsoft Foundry (GA)
┌─────────────────┐                 ┌──────────────────────────┐
│ Python CLI       │ ── HTTPS ────→ │ Foundry Agent Service    │
│ AIProjectClient  │                │  - Prompt Agent (versioned)│
│                  │                │  - Conversations          │
│ Local tools:     │                │  - Responses API          │
│  - list_docs     │ ←─ tool call ─ │  - Tool orchestration     │
│  - read_document │ ── output ───→ │                          │
│  - search_guides │                │ Visible in portal:        │
│                  │                │  Build → Agents           │
│ Auth:            │                │                          │
│  DefaultAzure-   │                │                          │
│  Credential      │                │                          │
└─────────────────┘                 └──────────────────────────┘
```

## Why the GA Agent Service (not Classic)

| Aspect | Classic (`azure-ai-agents`) | GA (`azure-ai-projects` 2.x) |
|--------|---------------------------|------------------------------|
| Status | Deprecated, retires Mar 31 2027 | Generally Available |
| Portal editability | Legacy view only | Fully editable in new portal |
| State model | Threads + Messages + Runs | Conversations + Responses API |
| Versioning | Manual | Built-in `create_version` |
| Tool definition | Auto from type hints | Explicit JSON schema (`strict: true`) |
