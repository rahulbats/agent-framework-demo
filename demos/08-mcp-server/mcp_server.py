"""
Demo 08: MCP server (real protocol)

Uses the official `mcp` Python SDK with the streamable-http transport (the
current MCP standard, replacing SSE). Three insurance-domain tools are exposed
so an agent can look up policies, search underwriting guidelines, and pull
loss-run history without each agent having to bake in a different REST client.

Run:
    python mcp_server.py
        -> http://localhost:8081/mcp

Verify with the inspector (optional):
    npx @modelcontextprotocol/inspector
        connect to http://localhost:8081/mcp
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

# Streamable-HTTP server. host/port/path control the public URL the client
# (or an agent runtime) will connect to.
mcp = FastMCP(
    name="internal-apis",
    instructions=(
        "Insurance internal-API tools. Use get_policy to look up a policy by "
        "ID, search_guidelines to find underwriting programs by insurance "
        "type, and get_loss_runs to retrieve loss history for an insured."
    ),
    host="0.0.0.0",
    port=8081,
    streamable_http_path="/mcp",
)


# ---------- simulated internal data ----------------------------------------

POLICIES_DB = {
    "POL-001": {
        "id": "POL-001",
        "insured": "Acme Corp",
        "type": "Commercial Property",
        "limit": "$10M",
        "state": "Georgia",
    },
    "POL-002": {
        "id": "POL-002",
        "insured": "Beta Inc",
        "type": "Cyber Liability",
        "limit": "$5M",
        "state": "Texas",
    },
}

GUIDELINES_DB = [
    {
        "program": "CP-100",
        "type": "Commercial Property",
        "max_limit": "$15M",
        "states": ["GA", "FL", "TX"],
        "min_year": 1990,
    },
    {
        "program": "CY-200",
        "type": "Cyber Liability",
        "max_limit": "$25M",
        "states": ["All"],
        "min_year": 2000,
    },
]


# ---------- tools ----------------------------------------------------------
# FastMCP introspects the type annotations + docstring to build the MCP tool
# schema, so what the LLM sees is generated from the Python signature.


@mcp.tool()
def get_policy(policy_id: str) -> str:
    """Look up an insurance policy by its ID (e.g. POL-001)."""
    policy = POLICIES_DB.get(policy_id)
    if not policy:
        return json.dumps({"error": f"policy {policy_id} not found"})
    return json.dumps(policy)


@mcp.tool()
def search_guidelines(insurance_type: str) -> str:
    """Find underwriting programs that cover the given insurance type."""
    needle = insurance_type.lower()
    matches = [g for g in GUIDELINES_DB if needle in g["type"].lower()]
    return json.dumps(matches)


@mcp.tool()
def get_loss_runs(insured_name: str) -> str:
    """Return the loss-run history (5 yr) for an insured entity."""
    return json.dumps(
        {
            "insured": insured_name,
            "period": "2021-2025",
            "claims": [
                {"year": 2023, "type": "Property Damage", "amount": "$75,000", "status": "Closed"},
                {"year": 2024, "type": "Business Interruption", "amount": "$50,000", "status": "Closed"},
            ],
            "total_incurred": "$125,000",
        }
    )


if __name__ == "__main__":
    # streamable-http is the modern transport. Foundry/Claude/etc. all speak it.
    mcp.run(transport="streamable-http")
