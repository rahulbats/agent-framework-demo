# Framework Recommendation

## Recommendation: Microsoft Agent Framework (primary) + LangGraph (complex workflows)

### Quick Decision Guide

```
Is your agent a simple tool-calling assistant?
  → Use Agent Framework (Foundry-native, auto-OTEL, Agent Identity)

Does your agent need complex conditional branching with cycles?
  → Use LangGraph inside a Foundry Agent Service container

Do you need maximum ecosystem integrations (document loaders, vector stores)?
  → Use LangChain components as utilities within either framework
```

## Comparison

| Dimension | Agent Framework | LangGraph | LangChain |
|---|---|---|---|
| **Foundry Integration** | ✅ Native | ❌ Manual | ❌ Manual |
| **Session Isolation** | ✅ Managed | ❌ DIY | ❌ DIY |
| **Agent Identity** | ✅ Entra native | ❌ Manual | ❌ Manual |
| **Auto-OTEL Traces** | ✅ Built-in | ❌ Opt-in (LangSmith) | ❌ Opt-in (LangSmith) |
| **A2A Protocol** | ✅ Native | ❌ Custom | ❌ Custom |
| **Multi-Agent** | ✅ A2A delegation | ✅ Graph nodes | ❌ Needs LangGraph |
| **Complex Workflows** | ⚠️ Basic workflows | ✅ Best (DAG/cycles) | ❌ Sequential only |
| **Human-in-the-Loop** | ⚠️ Manual | ✅ First-class | ❌ Limited |
| **Checkpointing** | ⚠️ Session state | ✅ Per-node state | ❌ None |
| **Community Size** | ⚠️ Growing | ✅ Large | ✅ Largest |
| **Learning Resources** | ⚠️ MS Learn | ✅ Extensive | ✅ Most extensive |
| **Cloud Portability** | ❌ Azure only | ✅ Any cloud | ✅ Any cloud |
| **RAG Tooling** | ⚠️ Azure AI Search SDK | ✅ Many retrievers | ✅ Most retrievers |
| **Document Loaders** | ❌ Use DI SDK directly | ✅ 80+ loaders | ✅ 80+ loaders |
| **Cost (Observability)** | ✅ Free (App Insights) | 💰 LangSmith (paid SaaS) | 💰 LangSmith (paid SaaS) |

## Why Agent Framework as Primary

For enterprise insurance requirements, Agent Framework eliminates the most infrastructure work:

1. **Session isolation** is a top requirement → Agent Framework + Foundry gives VM-per-session for free
2. **Agent identity** (short-lived creds, no vault access) → Entra Agent Identity is native to Agent Framework
3. **Observability** (LangFuse-like traces, cost-per-session) → Auto-OTEL to App Insights, zero code
4. **Versioning** (blue-green, rollback) → Foundry Agent Service handles this, framework-agnostic
5. **A2A protocol** → Native, needed for supervisor + sub-agent pattern
6. **Kill switch** → Foundry API, works with Agent Framework agents

Reducing DIY infrastructure is high value for teams new to Azure.

## When to Use LangGraph

LangGraph is superior for one specific pattern: **the supervisor agent that dynamically decomposes tasks and delegates to sub-agents with conditional logic**.

Example where LangGraph shines:
```
Supervisor receives submission →
  1. Classify documents (parallel) →
  2. If classification confidence < 80%: retry with different prompt →
  3. If retry fails: escalate to human →
  4. Extract data fields →
  5. If missing required fields: ask user for clarification →
  6. Match against guidelines →
  7. If no match: try broader search →
  8. Compile recommendation
```

This has **cycles** (retry), **conditionals** (confidence threshold), and **human-in-the-loop** (escalation) — LangGraph's graph model handles this naturally.

**Key insight**: LangGraph can run **inside** a Foundry Agent Service container. You get LangGraph's orchestration + Foundry's managed infrastructure.

## What to Avoid

**Don't use LangChain standalone for orchestration**. LangChain's "agent" is a ReAct loop — it doesn't support the supervisor pattern needed here. Use LangChain components (document loaders, vector stores) as utilities within Agent Framework or LangGraph.

## Migration Path

```
Phase 1 (Now):     Agent Framework on local → Foundry LLMs
Phase 2 (Deploy):  Agent Framework on Foundry Agent Service
Phase 3 (Complex): LangGraph inside Foundry containers (for supervisor agent)
```

This lets teams start simple and add complexity only when needed — start simple, evolve in complexity.

## Framework Compatibility with Foundry Agent Service

All three frameworks can be deployed to Foundry Agent Service as containers:

| Framework | Foundry Deployment | Native Features | Manual Setup Needed |
|---|---|---|---|
| Agent Framework | ✅ First-class | Session isolation, OTEL, Identity, A2A, MCP | None |
| LangGraph | ✅ As container | Session isolation (infra-level) | OTEL, Identity, A2A |
| LangChain | ✅ As container | Session isolation (infra-level) | OTEL, Identity, A2A, orchestration |

Microsoft's stated position is that Foundry Agent Service is **framework-agnostic** — it supports Agent Framework, LangGraph, Semantic Kernel, and custom code. You are not locked in.
