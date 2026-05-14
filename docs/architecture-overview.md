# Architecture Overview

Visual architecture diagrams for all agent patterns. All diagrams use Mermaid and render in GitHub/VS Code.

## 1. Overall Architecture

```mermaid
graph TB
    subgraph Users["Users / Producers / Brokers"]
        U1[User 1]
        U2[User 2]
    end

    subgraph Gateway["Azure API Management (AI Gateway)"]
        APIM[APIM Policies]
        APIM -->|token-limit| TRL[Token Rate Limiting]
        APIM -->|emit-metric| TM[Token Metrics]
        APIM -->|cache| SC[Semantic Cache<br/>Redis]
        APIM -->|content-safety| CS[Content Safety]
    end

    subgraph Foundry["Azure AI Foundry"]
        subgraph AgentService["Agent Service (East US 2)"]
            AV1[Agent V1<br/>90% traffic]
            AV2[Agent V2<br/>10% traffic]
        end
        subgraph LLMs["Foundry LLMs"]
            GPT4[GPT-4o]
            Claude[Claude Sonnet 4.7]
        end
        EVAL[Foundry Evaluation]
    end

    subgraph Identity["Microsoft Entra"]
        AID[Agent Identity<br/>Blueprint]
        MI[Managed Identity]
    end

    subgraph Observability["Azure Monitor"]
        AI[Application Insights<br/>OTEL Traces]
        LA[Log Analytics]
        DASH[Agent Dashboard]
    end

    subgraph Tools["Agent Tools"]
        MCP[MCP Toolbox<br/>Foundry-managed]
        VNET[Internal APIs<br/>via VNet]
        SEARCH[Azure AI Search<br/>RAG]
        DI[Document Intelligence<br/>OCR]
    end

    subgraph CICD["CI/CD"]
        ADO[Azure DevOps]
        TF[Terraform]
    end

    U1 --> Gateway
    U2 --> Gateway
    Gateway --> AgentService
    Gateway --> LLMs
    AgentService --> LLMs
    AgentService --> Tools
    AgentService --> Identity
    AgentService -.->|auto-OTEL| Observability
    ADO --> EVAL
    ADO -->|deploy| AgentService
    TF -->|provision| Foundry
```

## 2. Session-Level Isolation

This diagram matches the whiteboard discussion from the design session. Each user session gets its own isolated agent instance — no data leakage between sessions.

```mermaid
sequenceDiagram
    participant U1 as User 1
    participant U2 as User 2
    participant RT as Agent Runtime<br/>(Foundry Agent Service)
    participant I1 as Instance A<br/>(Session S1)
    participant I2 as Instance B<br/>(Session S2)
    participant I3 as Instance C<br/>(Session S3)

    U1->>RT: Request (session=S1, "Analyze submission docs")
    RT->>I1: Route to new instance
    I1-->>U1: Response

    U2->>RT: Request (session=S2, "Check guidelines")
    RT->>I2: Route to new instance
    I2-->>U2: Response

    Note over I1,I2: S1 and S2 are fully isolated<br/>No shared memory or state

    U1->>RT: Follow-up (session=S1, "What about coverage?")
    RT->>I1: Route to SAME instance (multi-turn)
    I1-->>U1: Response with S1 context

    U1->>RT: New conversation (session=S3)
    RT->>I3: Route to NEW instance
    I3-->>U1: Fresh context, no S1 data
```

### Key Properties
- **VM-per-session**: Each session gets a dedicated micro-VM (not a shared container)
- **Multi-turn continuity**: Subsequent calls with the same session ID route to the same instance
- **30-day persistence**: Sessions persist up to 30 days with 15-min idle timeout
- **Auto state restore**: If an instance is recycled, state is restored transparently
- **No data leakage**: Producer team A's session cannot access Broker team B's session data

## 3. Agent Identity Flow

The agent never touches long-lived credentials (client secrets). Instead, the identity service handles token acquisition.

```mermaid
sequenceDiagram
    participant Agent as Agent<br/>(Foundry Runtime)
    participant EAI as Entra Agent<br/>Identity Service
    participant KV as Azure Key Vault
    participant AS as Authorization Server<br/>(Entra ID)
    participant Target as Target System<br/>(Omega API)

    Note over Agent: Agent needs to call Omega API

    Agent->>EAI: "I need a token for Omega"
    EAI->>KV: Retrieve client_id + client_secret<br/>(Agent never sees these)
    KV-->>EAI: Credentials
    EAI->>AS: Client credentials grant<br/>(client_id + client_secret)
    AS-->>EAI: Access token (short-lived, 1hr)
    EAI-->>Agent: Access token only

    Agent->>Target: API call + Bearer token
    Target-->>Agent: Response

    Note over Agent: Agent only ever had the<br/>short-lived access token
```

### Token Exchange Flow (Optional)

```mermaid
sequenceDiagram
    participant Agent as Agent
    participant EAI as Entra Agent<br/>Identity Service
    participant AS as Authorization Server

    Agent->>EAI: "Exchange my token for Omega-audience token"
    EAI->>AS: Token exchange request<br/>(grant_type=urn:ietf:params:oauth:grant-type:token-exchange)
    AS-->>EAI: New token with Omega audience
    EAI-->>Agent: Substituted token

    Agent->>Target: API call + substituted token
```

## 4. Multi-Agent Supervisor Pattern

Hierarchical agent design: a supervisor decomposes tasks and delegates to specialized sub-agents.

```mermaid
graph TB
    subgraph User["User Request"]
        REQ["Analyze this insurance<br/>submission package"]
    end

    subgraph Supervisor["Supervisor Agent"]
        SUP[Task Decomposition<br/>& Orchestration]
    end

    subgraph SubAgents["Sub-Agents (via A2A Protocol)"]
        DOC[Document Classifier<br/>Agent]
        EXT[Data Extractor<br/>Agent]
        MATCH[Guideline Matcher<br/>Agent]
    end

    subgraph Tools["Agent Tools"]
        DI[Document Intelligence<br/>OCR]
        SEARCH[Azure AI Search<br/>Guidelines RAG]
        DB[(Submission<br/>Database)]
    end

    REQ --> SUP
    SUP -->|"1. Classify documents"| DOC
    SUP -->|"2. Extract key fields"| EXT
    SUP -->|"3. Match guidelines"| MATCH

    DOC --> DI
    EXT --> DI
    MATCH --> SEARCH
    EXT --> DB
    MATCH --> DB

    DOC -->|"PDF: Loss Run Statement<br/>Word: Application Form<br/>Excel: Coverage Summary"| SUP
    EXT -->|"insured: Acme Corp<br/>coverage: $10M<br/>state: Georgia<br/>property: Multi-story"| SUP
    MATCH -->|"Product: Commercial Property<br/>Program: CP-100<br/>Fit Score: 87%"| SUP

    SUP -->|"Final recommendation<br/>with confidence score"| RESP[Response to User]
```

### Orchestration Details
- Supervisor uses **LLM reasoning** to decompose (not a hardcoded workflow)
- Sub-agents can be called **in parallel** when independent (classify + extract)
- Sub-agents can be called **sequentially** when dependent (extract → match)
- Supervisor **validates** sub-agent outputs and may retry or escalate
- All communication via **A2A protocol** (Agent Framework native)

## 5. CI/CD Pipeline Flow

```mermaid
graph LR
    subgraph Dev["Development"]
        CODE[Agent Code] --> COMMIT[Git Commit]
    end

    subgraph Build["Build Stage"]
        COMMIT --> DOCKER[Docker Build]
        DOCKER --> ACR[Push to ACR]
        DOCKER --> UNIT[Unit Tests]
    end

    subgraph Evaluate["Evaluation Gate"]
        ACR --> DEPLOY_DEV[Deploy to Dev<br/>Foundry Agent Service]
        DEPLOY_DEV --> EVAL[Run Foundry<br/>Evaluation]
        EVAL --> GATE{Quality Gate<br/>Precision ≥ 70%?}
    end

    subgraph Deploy["Production Deploy"]
        GATE -->|Pass| CANARY[Canary Deploy<br/>10% traffic]
        GATE -->|Fail| BLOCK[Block Release]
        CANARY --> MONITOR[Monitor Metrics<br/>30 min bake]
        MONITOR --> GATE2{Error Rate<br/>< 5%?}
        GATE2 -->|Pass| FULL[Full Deploy<br/>100% traffic]
        GATE2 -->|Fail| ROLLBACK[Rollback to<br/>Previous Version]
    end

    subgraph Observe["Post-Deploy"]
        FULL --> DASH[Agent Dashboard<br/>App Insights]
        FULL --> CONT_EVAL[Continuous<br/>Evaluation]
        CONT_EVAL --> ALERT[Alert on<br/>Regression]
    end

    style BLOCK fill:#f55,stroke:#333
    style ROLLBACK fill:#fa0,stroke:#333
    style FULL fill:#5f5,stroke:#333
```

## 6. LLM Gateway (APIM) Architecture

Answers: "Is there a global endpoint that routes to any Claude Sonnet 4.7 in US?"

```mermaid
graph TB
    subgraph Agents["Agent Requests"]
        A1[Agent 1<br/>East US 2]
        A2[Agent 2<br/>East US 2]
    end

    subgraph APIM["APIM AI Gateway"]
        LB[Load Balancer<br/>Priority-based]
        CB[Circuit Breaker<br/>Retry-After aware]
        CACHE[Semantic Cache<br/>Redis-backed]
        METRICS[Token Metrics<br/>per-agent, per-session]
        RL[Token Rate Limit<br/>TPM per consumer]
    end

    subgraph Backends["LLM Backends (Priority Order)"]
        B1[Claude Sonnet 4.7<br/>East US 2<br/>Priority: 1]
        B2[Claude Sonnet 4.7<br/>East US<br/>Priority: 2]
        B3[Claude Sonnet 4.7<br/>West US 3<br/>Priority: 3]
    end

    A1 --> APIM
    A2 --> APIM
    APIM --> |"1. Check cache"| CACHE
    CACHE -->|miss| LB
    LB --> B1
    B1 -->|429 throttled| CB
    CB -->|failover| B2
    B2 -->|429 throttled| CB
    CB -->|failover| B3
```

### How It Works
1. Agent sends LLM request to APIM gateway (single endpoint)
2. APIM checks **semantic cache** — if similar prompt was seen, return cached response (60-80% cost savings)
3. On cache miss, route to **primary backend** (East US 2, lowest latency)
4. If primary returns **429 (throttled)**, circuit breaker triggers **automatic failover** to next priority
5. **Token metrics** emitted per agent, per session, per consumer for cost attribution
6. **Token rate limiting** enforced per subscription to prevent runaway costs

## 7. Kill Switch Architecture

```mermaid
sequenceDiagram
    participant Agent as Agent V2<br/>(Production)
    participant Monitor as Azure Monitor
    participant Alert as Alert Rule<br/>(cost > $100/session)
    participant Logic as Logic App /<br/>Function
    participant API as Foundry API

    Note over Agent: Agent processing sessions...

    Agent->>Monitor: Emit cost metrics per session
    Monitor->>Alert: Session S42 cost = $105
    Alert->>Logic: Trigger automated response

    alt Kill Session Only
        Logic->>API: DELETE /sessions/S42
        API-->>Agent: Terminate session S42
        Note over Agent: S42 terminated<br/>Other sessions unaffected
    else Kill Agent Version
        Logic->>API: PATCH /agents/submission-v2<br/>{status: "disabled"}
        API-->>Agent: Disable all V2 traffic
        Note over Agent: All V2 traffic routed<br/>back to V1
    end
```
