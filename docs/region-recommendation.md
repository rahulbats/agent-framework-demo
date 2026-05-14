# Region Recommendation

## Recommendation: East US 2

**Primary region for all agent workloads: East US 2**

### Rationale

| Factor | East US 2 | West US 3 | Central US |
|---|---|---|---|
| **Production Data** | ✅ Primary data region | ❌ Cross-region latency | ✅ Secondary region |
| **Users (East Coast)** | ✅ Low latency (~10ms) | ❌ High latency (~60ms) | ⚠️ Medium latency (~30ms) |
| **Foundry Agent Service** | ✅ Available | ✅ Available | ✅ Available |
| **Foundry LLMs (GPT-4o)** | ✅ Available | ✅ Available | ✅ Available |
| **Claude Sonnet 4.7** | ✅ Available | ✅ Available | ⚠️ Check availability |
| **APIM** | ✅ Available | ✅ Available | ✅ Available |
| **Document Intelligence** | ✅ Available | ✅ Available | ✅ Available |
| **Azure AI Search** | ✅ Available | ✅ Available | ✅ Available |
| **Data Colocation** | ✅ Same region as data | ❌ Agent → data cross-region | ⚠️ Depends on setup |

### Latency Analysis

If agents run in West US 3 but data is in East US 2, every agent → MCP/data call adds ~50-60ms round-trip. For a typical agent loop with 3-5 tool calls, that's 150-300ms of unnecessary latency per invocation.

```
Scenario A (RECOMMENDED): All in East US 2
User (East) → Agent (East US 2) → Data (East US 2)
Total network overhead: ~20ms

Scenario B (AVOID): Agent in West, Data in East
User (East) → Agent (West US 3) → Data (East US 2) → Agent (West US 3) → User
Total network overhead: ~180ms (3x tool calls × 60ms)
```

### Action Items

1. **Confirm with platform team** that East US 2 is approved for new workloads
2. **Foundry LLMs**: Verify Claude Sonnet 4.7 availability in East US 2 (available as of May 2026)
3. **Failover region**: Use Central US as secondary for disaster recovery

### If East US 2 Is Not Approved

If the platform team restricts new regions, provide this justification:

> "Azure AI Foundry Agent Service is available in East US 2, which is the primary data region. Running agents in a different region (e.g., West US 3) introduces 50-60ms latency per tool call, degrading agent response times by 150-300ms per invocation. Collocating agents with data in East US 2 is the recommended architecture for performance and data residency."
