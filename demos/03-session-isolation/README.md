# Demo 03: Session-Level Isolation

Proves that two concurrent users of the **same Foundry agent** get fully
isolated conversation state — no cross-session data leakage.

## What This Demonstrates

- Two parallel **Foundry Conversations** (`conv_*`) opened against the
  `insurance-submission-agent` deployed in [Lab 1](../01-local-agent-dev/).
- Each conversation pins a different agent version (`v1` and `v2`) via
  `agent_reference`, exercising the same A/B pair from Lab 1.
- Distinct broker data is injected into each session (Acme Corp / Georgia
  vs. Beta Industries / Texas).
- A recall turn asks each session to restate its own data.
- Six automated assertions verify S1 never leaks into S2 and vice versa.

Isolation is enforced server-side by Foundry: each `conversation_id` is its
own scope. The agent definition (instructions, tools, model) is shared, but
**conversation state is not**.

## Prerequisites

1. Lab 1 must have been run at least once so that
   `insurance-submission-agent` exists with **at least two versions**
   (v1 + v2). The demo picks the two highest versions automatically.
2. `az login` completed.
3. `.env` at `../01-local-agent-dev/.env` with `FOUNDRY_ENDPOINT` set.

## Run

```powershell
python demo_session_isolation.py
```

## Expected Output

```
Demo 03 — Session-Level Isolation (Foundry Conversations)
Agent: insurance-submission-agent

  Using insurance-submission-agent v1 (S1) and v2 (S2)
  S1 conversation: conv_58199a4d...
  S2 conversation: conv_5ab69845...

Step 1: Send distinct broker data to each session
  S1 (v1): I have noted the submission details: Acme Corp, $10M ...
  S2 (v2): Noted: Beta Industries, $5M cyber liability, Texas ...

Step 2: Ask each session to recall its own data
  S1 recall: Acme Corp / $10M / Commercial Property / Georgia
  S2 recall: Beta Industries / $5M / Cyber Liability / Texas

Step 3: Verify no cross-session leakage
      Session Isolation Verification
  ┌────────────────────────────────┬────────┐
  │ S1 remembers 'Acme Corp'       │  PASS  │
  │ S1 does NOT mention 'Beta'     │  PASS  │
  │ S1 does NOT mention 'Texas'    │  PASS  │
  │ S2 remembers 'Beta Industries' │  PASS  │
  │ S2 does NOT mention 'Acme'     │  PASS  │
  │ S2 does NOT mention 'Georgia'  │  PASS  │
  └────────────────────────────────┴────────┘

Session isolation verified.
```

Both conversations are deleted on exit. The agent versions in Foundry are
left intact for the other labs.

## Key Code

```python
# Two independent conversation scopes against the same agent
s1 = openai_client.conversations.create().id
s2 = openai_client.conversations.create().id

# Each call pins which version of the agent handles the turn
openai_client.responses.create(
    input=prompt,
    conversation=s1,                      # <-- scope
    extra_body={"agent_reference": {
        "name": AGENT_NAME,
        "version": str(v1),               # <-- version
        "type": "agent_reference",
    }},
)
```

## Why This Matters

In multi-tenant agent deployments, accidentally sharing state across users
is a critical safety failure (PII leakage, cross-tenant data exposure).
Foundry's `conversation_id` is the isolation boundary — this lab is the
proof.
