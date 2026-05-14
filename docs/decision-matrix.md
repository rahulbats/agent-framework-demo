# Agent Runtime Decision Matrix

This document compares Azure services for hosting AI agents against common enterprise requirements. Use this to determine which service best fits your deployment needs.

## Recommendation Summary

| Scenario | Recommended Service | Why |
|---|---|---|
| **Default agent hosting** | Azure AI Foundry Agent Service | Managed session isolation, versioning, OTEL, agent identity |
| **Complex multi-agent / GPU** | Azure Kubernetes Service (AKS) | Full control, GPU support, advanced networking |
| **Variable-load microservices** | Azure Container Apps (ACA) | Scale-to-zero, cost-efficient, revision-based versioning |
| **Simple HTTP agents** | Azure App Service | Familiar PaaS, deployment slots, but limited agent features |

## Full Comparison

| Dimension | Foundry Agent Service | Container Apps | AKS | App Service |
|---|---|---|---|---|
| **Container Support** | ✅ Docker → ACR → Foundry | ✅ Any OCI container | ✅ Any OCI container | ⚠️ Custom containers (Linux only) |
| **Agent Versioning** | ✅ Immutable versions, canary/blue-green traffic split | ✅ Revisions with traffic splitting | ✅ Rolling updates, Helm releases, Argo Rollouts | ⚠️ Deployment slots (swap-based) |
| **Blue-Green / Canary** | ✅ Built-in traffic % routing | ✅ Built-in traffic % routing | ✅ Via Istio / Argo Rollouts | ⚠️ Slot swaps (all-or-nothing) |
| **Rollback** | ✅ Route 100% back to previous version | ✅ Activate previous revision | ✅ `helm rollback` / `kubectl rollout undo` | ⚠️ Swap back to staging slot |
| **Session-Level Isolation** | ✅ VM-per-session (30-day persist, auto state restore) | ⚠️ Session affinity (sticky sessions, shared process) | ⚠️ Session affinity via Service (shared pod) | ❌ No session isolation |
| **VNet Integration** | ✅ Network-isolated Foundry + customer VNet for outbound | ✅ Managed VNet + customer VNet | ✅ Advanced (Azure CNI Overlay, egress gateways) | ✅ VNet integration (outbound only) |
| **Egress to Internal Network** | ✅ Via customer VNet peering | ✅ Via VNet peering | ✅ Full VNet control | ✅ Via VNet integration |
| **Egress to Internet** | ✅ Via VNet NAT gateway | ✅ Via VNet NAT gateway | ✅ Via egress gateway / NAT | ✅ Default outbound |
| **Managed Identity** | ✅ Per-agent Entra ID auto-provisioned | ✅ System/user-assigned MI | ✅ Workload Identity (pod-level) | ✅ System/user-assigned MI |
| **Agent Identity (Short-Lived Creds)** | ✅ Entra Agent Identity Blueprint | ⚠️ Manual (MI + Key Vault) | ⚠️ Manual (Workload Identity + Key Vault) | ⚠️ Manual (MI + Key Vault) |
| **OTEL / Observability** | ✅ Auto-injected App Insights, traces out-of-box | ⚠️ Manual OTEL SDK setup | ⚠️ Manual (Container Insights + OTEL) | ⚠️ Manual App Insights SDK |
| **Cost Per Session** | ✅ Built-in session-level cost tracking | ❌ Custom implementation | ❌ Custom implementation | ❌ Custom implementation |
| **Kill Switch (Agent)** | ✅ Disable version via API | ⚠️ Scale revision to 0 | ⚠️ Scale deployment to 0 | ⚠️ Stop slot |
| **Kill Switch (Session)** | ✅ Terminate session via API | ❌ Custom implementation | ❌ Custom implementation | ❌ Custom implementation |
| **A2A Protocol** | ✅ Native support (agent delegation) | ❌ Custom implementation | ❌ Custom implementation | ❌ Custom implementation |
| **MCP Server Hosting** | ✅ Foundry Toolbox (managed MCP endpoint) | ⚠️ Host as container (manual) | ⚠️ Host as container (manual) | ⚠️ Host as web app (manual) |
| **AG-UI Protocol** | 🔜 Roadmap | ❌ Not applicable | ❌ Not applicable | ❌ Not applicable |
| **Multi-Agent Orchestration** | ✅ A2A protocol + Agent Framework workflows | ⚠️ Service-to-service calls | ✅ Service mesh (Istio), pod-to-pod | ⚠️ App-to-app HTTP calls |
| **Agent Evaluation** | ✅ Foundry Evaluation (offline + continuous) | ❌ External tooling needed | ❌ External tooling needed | ❌ External tooling needed |
| **CI/CD Integration** | ✅ Azure DevOps / GitHub Actions native | ✅ Azure DevOps / GitHub Actions | ✅ Azure DevOps / GitHub Actions / ArgoCD | ✅ Azure DevOps / GitHub Actions |
| **LLM Routing / Failover** | ⚠️ Via APIM AI Gateway (separate service) | ⚠️ Via APIM AI Gateway | ⚠️ Via APIM AI Gateway | ⚠️ Via APIM AI Gateway |
| **Semantic Caching** | ⚠️ Via APIM AI Gateway | ⚠️ Via APIM AI Gateway | ⚠️ Via APIM AI Gateway | ⚠️ Via APIM AI Gateway |
| **Auto-Scaling** | ✅ Per-session scaling (consumption-based) | ✅ KEDA event-driven, scale-to-zero | ✅ HPA, KEDA, cluster autoscaler | ⚠️ Manual or rule-based |
| **GPU Support** | ❌ Not available | ⚠️ Limited (preview) | ✅ Full (GPU node pools) | ❌ Not available |
| **Region: East US 2** | ✅ Available | ✅ Available | ✅ Available | ✅ Available |
| **Pricing Model** | 💰 CPU/memory per active session | 💰 vCPU/memory per second (scale-to-zero) | 💰 VM node pools (always-on base) | 💰 App Service Plan (always-on) |
| **Framework Lock-in** | ❌ Framework-agnostic (AF, LangGraph, custom) | ❌ Framework-agnostic | ❌ Framework-agnostic | ❌ Framework-agnostic |

### Legend
- ✅ Supported out-of-box
- ⚠️ Supported with manual work / partial
- ❌ Not supported
- 🔜 On roadmap

## Platform-Specific Analysis

### What Foundry Agent Service Cannot Do (Gaps)

| Gap | Workaround | Timeline |
|---|---|---|
| **GPU workloads** | Use AKS with GPU node pools | N/A — by design |
| **Private ACR** | Use public ACR endpoint (with network rules) | 🔜 Private ACR support expected |
| **AG-UI protocol** | Custom WebSocket implementation | 🔜 Roadmap |
| **Complex DAG workflows** | Use LangGraph inside Foundry container | N/A — use LangGraph |

### When to Choose AKS Instead

1. Agent requires **GPU** for local model inference
2. Need **Istio service mesh** for complex agent-to-agent traffic management
3. Require **on-premises hybrid** via Azure Arc
4. Need **more than 4 GiB RAM** per agent instance
5. Have existing AKS infrastructure and Kubernetes expertise

### When to Choose Container Apps Instead

1. Need **scale-to-zero** for cost optimization on dev/test agents
2. Running **non-agent** microservices alongside agents
3. Want simpler Kubernetes experience without cluster management
4. Event-driven agents triggered by **Service Bus / Event Grid**

## Recommended Architecture

```
Internet/Users
      │
      ▼
┌─────────────────┐
│  APIM AI Gateway │  ← LLM routing, caching, cost tracking, guardrails
│  (Global)        │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────────────────┐
│Foundry │ │ Foundry Agent Service │ ← Primary agent runtime
│  LLMs  │ │ (East US 2)           │
└────────┘ │  ├─ Agent V1 (90%)    │
           │  ├─ Agent V2 (10%)    │
           │  ├─ Session Isolation  │
           │  ├─ Auto-OTEL         │
           │  └─ Agent Identity     │
           └──────────┬───────────┘
                      │
              ┌───────┴───────┐
              ▼               ▼
        ┌──────────┐   ┌──────────┐
        │ MCP Tools│   │Internal  │
        │(Foundry  │   │Systems   │
        │ Toolbox) │   │(via VNet)│
        └──────────┘   └──────────┘
```
