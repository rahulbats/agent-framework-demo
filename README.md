# Agent Framework Demo

A comprehensive demo project showing AI agent architecture patterns on Azure. Each demo module is self-contained and answers a specific architecture question.

## Quick Navigation

### 📄 Documentation (Start Here)

| Document | What It Answers |
|---|---|
| [Decision Matrix](docs/decision-matrix.md) | "What Azure service should we use for agent runtime?" — Foundry vs ACA vs AKS vs App Service |
| [Architecture Overview](docs/architecture-overview.md) | Visual architecture diagrams for all patterns (session isolation, identity, multi-agent, CI/CD) |
| [Region Recommendation](docs/region-recommendation.md) | "Which region should we run this on?" — East US 2 analysis |
| [Framework Recommendation](docs/framework-recommendation.md) | "Should we use LangChain, LangGraph, or Agent Framework?" |

### 🔧 Demos

| # | Demo | Question Answered |
|---|---|---|
| 01 | [Local Agent Dev](demos/01-local-agent-dev/) | Phase 1: Local agent → Azure Foundry LLMs, OpenAI function calling |
| 02 | [Containerized Agent](demos/02-containerized-agent/) | "Container support? Versioning? Blue-green deployment?" |
| 03 | [Session Isolation](demos/03-session-isolation/) | "Can the runtime support session-level isolation?" |
| 04 | [Agent Identity](demos/04-agent-identity/) | "Prevent agents from accessing long-lived credentials" |
| 05 | [Observability](demos/05-observability/) | "Agent traces like LangFuse? Cost per session?" |
| 06 | [Kill Switch](demos/06-kill-switch/) | "How to stop an agent version or kill a session?" |
| 07 | [LLM Gateway](demos/07-llm-gateway/) | "Global endpoint? Route to any Anthropic Claude Sonnet 4.7 in US?" |
| 08 | [MCP Server](demos/08-mcp-server/) | "Can Foundry run an MCP proxy of a REST API?" |
| 09 | [Multi-Agent](demos/09-multi-agent/) | "Supervisor agent + sub-agents, A2A protocol?" |
| 10 | [Agent Evaluation](demos/10-agent-evaluation/) | "Quality gate for production? Regression testing?" |
| 11 | [Agent Guardrails](demos/11-agent-guardrails/) | "Agent guardrails in addition to LLM guardrails?" |
| 12 | [CI/CD Pipeline](demos/12-cicd-pipeline/) | "Azure DevOps pipeline with agent eval as quality gate?" |
| 13 | [Foundry IQ](demos/13-foundry-iq/) | "Federated knowledge over SharePoint + AI Search + Blob without per-source RAG?" |
| 14 | [Batch Inference](demos/14-batch-inference/) | "Overnight triage on thousands of submissions at 50% cost, off the live deployment?" |
| 15 | [Document Intelligence](demos/15-document-intelligence/) | "PDFs / ACORDs / scanned loss runs → structured payload for the agent?" |

### 🏗️ Infrastructure

| Module | Purpose |
|---|---|
| [Terraform](infra/terraform/) | IaC modules for Foundry, APIM, networking, monitoring |

### 🚀 Accelerators

| Module | Purpose |
|---|---|
| [Submission Agent](accelerators/submission-agent/) | Insurance submission processing reference implementation |

## Prerequisites

- Python 3.11+
- Azure CLI (`az`) logged in
- Azure subscription with access to Azure AI Foundry
- Docker (for containerized demos)
- Terraform 1.5+ (for infrastructure demos)

## Getting Started

```bash
# Clone and set up
cd agent-framework-demo

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # Linux/Mac

# Install common dependencies
pip install -r requirements.txt

# Copy environment template
copy .env.example .env
# Edit .env with your Azure Foundry endpoint and key

# Run the first demo
cd demos/01-local-agent-dev
python main.py
```

## Architecture Summary

**Primary Recommendation: Azure AI Foundry Agent Service** — Managed runtime with session isolation, versioning, container support, auto-OTEL, and agent identity built-in.

**AI Gateway: Azure API Management** — LLM routing, semantic caching, token cost tracking, MCP governance.

**Complex Workloads: AKS** — GPU, advanced networking, multi-agent orchestration at scale.

See [Decision Matrix](docs/decision-matrix.md) for the full comparison.
