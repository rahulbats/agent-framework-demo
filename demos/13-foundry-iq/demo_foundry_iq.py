"""Demo 13 — Foundry IQ: federated knowledge for the insurance agent.

Two things in one script:

1. retrieve()       — call the Foundry IQ retrieval API directly. This is
                      what a search box / batch enrichment job would use.
2. compare_agents() — invoke insurance-submission-agent v14 (no KB) and
                      v15 (KB attached) on the same submission and print
                      both answers so the grounding lift is visible.

Prereqs (one-time, in the Azure AI Foundry portal):
  - Knowledge Sources created over the files in demos/01-local-agent-dev/data/
  - Knowledge Base 'insurance-underwriting-kb' bundling those sources
  - Tool 'grounding_with_knowledge_base' attached to insurance-submission-agent
    -> creates a new agent version (we assume v15 below; override via env).

See README.md for the portal walk-through.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env")

PROJECT_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"].rstrip("/")
KB_NAME = os.getenv("FOUNDRY_KB_NAME", "insurance-underwriting-kb")
AGENT_NAME = os.getenv("FOUNDRY_AGENT_NAME", "insurance-submission-agent")
AGENT_VERSION_BASE = int(os.getenv("FOUNDRY_AGENT_VERSION_BASE", "14"))   # no KB
AGENT_VERSION_KB = int(os.getenv("FOUNDRY_AGENT_VERSION_KB", "15"))       # KB attached

SUBMISSION = (
    "Insured: Acme Logistics LLC. Requesting commercial auto coverage for a "
    "fleet of 12 box trucks operating in TX and OK. Three at-fault losses in "
    "the last 24 months totaling $87k. Driver MVRs attached. "
    "Question: do they meet our underwriting guidelines, and what conditions "
    "should we attach to a quote?"
)

QUERIES = [
    "What is the maximum acceptable loss ratio for commercial auto risks?",
    "Are there geographic restrictions on box truck fleets in Texas?",
    "What MVR violations disqualify a driver from coverage?",
]


# ---------------------------------------------------------------------------
# 1. Direct retrieval — no agent in the loop
# ---------------------------------------------------------------------------

async def retrieve(query: str, *, top: int = 3) -> dict:
    """Call the Foundry IQ retrieval API directly.

    POST {project_endpoint}/knowledgeBases/{kb}/retrieve?api-version=...
    Body: {"query": "...", "top": N}
    Returns: {"results": [{"content": "...", "score": 0.87, "citations": [...]}]}
    """
    async with DefaultAzureCredential() as cred:
        token = (await cred.get_token("https://ai.azure.com/.default")).token

    url = f"{PROJECT_ENDPOINT}/knowledgeBases/{KB_NAME}/retrieve"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            params={"api-version": "2025-05-01-preview"},
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "top": top, "includeCitations": True},
        )
        resp.raise_for_status()
        return resp.json()


async def demo_retrieval() -> None:
    print("=" * 70)
    print(f"FOUNDRY IQ RETRIEVAL  (kb: {KB_NAME})")
    print("=" * 70)
    for q in QUERIES:
        print(f"\nQ: {q}")
        result = await retrieve(q)
        for i, hit in enumerate(result.get("results", []), 1):
            score = hit.get("score", 0.0)
            content = (hit.get("content") or "").strip().replace("\n", " ")
            cites = hit.get("citations", []) or []
            src = cites[0].get("source", "?") if cites else "?"
            print(f"  [{i}] ({score:.2f}) {content[:180]}…")
            print(f"      source: {src}")


# ---------------------------------------------------------------------------
# 2. Agent comparison — v14 (no KB) vs v15 (KB attached)
# ---------------------------------------------------------------------------

async def ask_agent(version: int, query: str) -> str:
    """Invoke a specific version of the hosted agent and return its text."""
    from agent_framework.foundry import FoundryAgent

    async with DefaultAzureCredential() as cred:
        agent = FoundryAgent(
            project_endpoint=PROJECT_ENDPOINT,
            agent_name=AGENT_NAME,
            agent_version=version,
            credential=cred,
        )
        response = await agent.run(query)
        return response.text or ""


async def demo_agent_compare() -> None:
    print("\n" + "=" * 70)
    print(f"AGENT COMPARE  v{AGENT_VERSION_BASE} (no KB) vs v{AGENT_VERSION_KB} (KB)")
    print("=" * 70)
    print(f"\nSubmission:\n  {SUBMISSION}\n")

    base, kb = await asyncio.gather(
        ask_agent(AGENT_VERSION_BASE, SUBMISSION),
        ask_agent(AGENT_VERSION_KB, SUBMISSION),
    )

    print("-" * 70)
    print(f"v{AGENT_VERSION_BASE}  (no knowledge base)")
    print("-" * 70)
    print(base)
    print("\n" + "-" * 70)
    print(f"v{AGENT_VERSION_KB}  (Foundry IQ knowledge base attached)")
    print("-" * 70)
    print(kb)


async def main() -> None:
    await demo_retrieval()
    await demo_agent_compare()


if __name__ == "__main__":
    asyncio.run(main())
