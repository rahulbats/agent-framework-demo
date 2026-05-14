# Demo 09: Multi-agent submission triage with human-in-the-loop

Real **Microsoft Agent Framework** workflow (`agent_framework` ≥ 1.3) that
chains three LLM-backed agents and gates the final answer behind a human
underwriter approval step.

## Pipeline

```
classifier  ──▶  extractor  ──▶  matcher  ──▶  human_approval  ──▶ FinalDecision
   (LLM)         (LLM + MCP)     (LLM + MCP)    (custom Executor)
     │               │                │                │
     └─── all three LLM calls go through APIM (Lab 7) ─┘
                     │                │
                     └──── MCP tools from Lab 8 ──────┘
```

| Stage | Type | What it does |
|-------|------|--------------|
| `classifier` | `AgentExecutor` wrapping `Agent` | Classifies the inbound broker email — insured, industry, lines, urgency. |
| `extractor` | `AgentExecutor` with `MCPStreamableHTTPTool` | Calls `get_policy`, `get_loss_runs`, `search_guidelines` on the Lab 8 MCP server. |
| `matcher` | `AgentExecutor` with `MCPStreamableHTTPTool` | Produces the underwriting recommendation (`auto_approve` / `refer_to_underwriter` / `decline`) with rationale, flags, and proposed terms. |
| `human_approval` | Custom `Executor` | **Pauses the workflow**, prints the recommendation, and prompts the human for `[a]pprove`, `[e]dit`, or `[r]eject`. Only then yields the workflow output. |

All chat traffic uses `OpenAIChatCompletionClient` pointed at the APIM AI
Gateway from Lab 7 — APIM enforces the subscription key, applies content
safety, and emits per-agent telemetry via the `x-agent-name` header.

## Why this composition

This lab is the capstone for everything before it:

- **Lab 7 APIM** centralises auth, safety, token metering for every model call.
- **Lab 8 MCP** is the system-of-record adapter: extractor and matcher don't
  hard-code data, they call real MCP tools.
- **Lab 9** wires those pieces into a `WorkflowBuilder` chain and adds the
  human gate that real underwriting requires.

## Real APIs used

```python
from agent_framework import (
    Agent, AgentExecutor, AgentExecutorRequest, AgentExecutorResponse,
    Executor, MCPStreamableHTTPTool, WorkflowBuilder, WorkflowContext,
    handler, response_handler,
)
from agent_framework.openai import OpenAIChatCompletionClient

workflow = (
    WorkflowBuilder(start_executor=classifier_node)
    .add_chain([classifier_node, extractor_node, matcher_node, approval_node])
    .build()
)
```

### Real human-in-the-loop (suspend / resume)

`HumanApprovalExecutor` does **not** block on `input()` inside the workflow.
It calls `ctx.request_info(...)`, which causes `workflow.run()` to **suspend**
and return a `request_info` event. The host code is responsible for delivering
that request to a human and resuming the workflow with the answer:

```python
class HumanApprovalExecutor(Executor):
    @handler
    async def review(self, message: AgentExecutorResponse,
                     ctx: WorkflowContext[Never, FinalDecision]) -> None:
        await ctx.request_info(
            request_data=ApprovalRequest(recommendation=message.agent_response.text),
            response_type=HumanDecision,
        )

    @response_handler
    async def on_decision(self, request: ApprovalRequest, response: HumanDecision,
                          ctx: WorkflowContext[Never, FinalDecision]) -> None:
        await ctx.yield_output(FinalDecision(...))
```

Host loop in `main()`:

```python
result = await workflow.run(initial_request)
while True:
    pending = result.get_request_info_events()    # suspended HITL requests
    if not pending:
        break
    responses = {}
    for ev in pending:
        decision = await notify_human(ev.data, ev.request_id)   # <-- pluggable
        responses[ev.request_id] = decision
    result = await workflow.run(responses=responses)             # resume
```

### How the human is notified

The default `notify_human()` prints to the terminal and reads `input()` so the
demo runs end-to-end on one machine. The function is the **single seam** for
swapping in any real channel — stubbed examples in the source:

| Channel | What goes out | What comes back |
|---------|---------------|-----------------|
| Terminal (default) | `print()` of recommendation | `input()` |
| Microsoft Teams Incoming Webhook | POST card with recommendation + `request_id` | A small approval portal writes the answer to a queue/DB keyed by `request_id`; `notify_human` polls/awaits it |
| Email (SMTP / Microsoft Graph) | Email with magic links per option | Magic-link handler resolves a future awaited by `notify_human` |
| Custom web UI | WebSocket push / SSE | UI POSTs `HumanDecision` back |

Because the workflow is genuinely suspended (not blocking a thread on
`input()`), the underwriter can take **hours or days** to respond — the host
process can persist `request_id` + state, exit, and a different process can
later call `workflow.run(responses={...})` to resume from a checkpoint.

## Prereqs

1. Lab 7 deployed — APIM gateway URL + subscription key in
   `demos/01-local-agent-dev/.env` as `APIM_GATEWAY_URL` and
   `APIM_SUBSCRIPTION_KEY`.
2. Lab 8 MCP server running locally:
   ```powershell
   .\.venv\Scripts\python.exe demos\08-mcp-server\mcp_server.py
   ```
3. SDK installed:
   ```powershell
   pip install agent-framework
   ```

## Run

```powershell
.\.venv\Scripts\python.exe demos\09-multi-agent\demo_multi_agent.py
```

You'll see the three agents execute, then a prompt tagged with the real
`request_id` the workflow emitted when it suspended:

```
HUMAN-IN-THE-LOOP  —  request ab2a9d37-176e-49a6-a26a-b93bda914b53
Matcher's proposed recommendation:
  { "recommended_action": "refer_to_underwriter", ... }

Options:  [a]pprove   [e]dit   [r]eject
Your decision (a/e/r):
```

- `a` — `HumanDecision(decision="approved")` → `FinalDecision(decision="approved", ...)`
- `e` — paste replacement JSON, then a comment → `decision="edited"`
- `r` — type a reason → `decision="rejected"`

## Files

- [demo_multi_agent.py](demo_multi_agent.py) — workflow + suspend/resume HITL executor + pluggable `notify_human`
