"""
Demo 08 — Scenario C: Azure API Center as the MCP registry.

Flow:
  1. Authenticate to ARM with DefaultAzureCredential (uses your `az login`).
  2. List APIs registered in the API Center workspace.
  3. Filter to kind == 'mcp'  (this is how an agent would discover available
     MCP servers in production — no hard-coded URLs).
  4. Read the selected API's deployment to get the runtime URL.
  5. Open an MCP session against that URL and run the same agent loop as
     Scenario B (gpt-4o via APIM picks tools, we execute over MCP).

Prereqs:
  - `az login` to a subscription that contains the API Center
  - `az deployment group create -f automation/api_center.bicep ...` already ran
  - mcp_server.py running on localhost:8081 (matches the registered URI)
  - APIM gateway from Lab 7 deployed

Run:
    python demo_mcp_registry.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import AzureOpenAI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env")
console = Console()

SUBSCRIPTION = os.environ.get(
    "AZURE_SUBSCRIPTION_ID", "e708e606-ba77-4156-8003-44fd88b6aa08"
)
RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "agent-framework-demo")
API_CENTER = os.environ.get("API_CENTER_NAME", "rahul-api-center")
WORKSPACE = os.environ.get("API_CENTER_WORKSPACE", "default")
WANTED_API_ID = os.environ.get("API_CENTER_API_ID", "internal-apis")

GATEWAY = os.environ["APIM_GATEWAY_URL"].rstrip("/")
APIM_KEY = os.environ["APIM_SUBSCRIPTION_KEY"]
DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o")
API_VERSION = "2024-12-01-preview"

ARM = "https://management.azure.com"
APIC_API_VERSION = "2024-06-01-preview"


# ---------- registry discovery --------------------------------------------


def _arm_get(token: str, path: str) -> dict[str, Any]:
    url = f"{ARM}{path}?api-version={APIC_API_VERSION}"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def discover_mcp_url() -> tuple[str, dict[str, Any]]:
    """Return (runtime_uri, api_metadata) for the registered MCP API."""
    cred = DefaultAzureCredential()
    token = cred.get_token("https://management.azure.com/.default").token

    base = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.ApiCenter/services/{API_CENTER}"
        f"/workspaces/{WORKSPACE}"
    )

    apis = _arm_get(token, f"{base}/apis")["value"]

    table = Table(title="API Center catalog", show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("kind")
    table.add_column("title")
    for a in apis:
        table.add_row(a["name"], a["properties"].get("kind", "?"), a["properties"].get("title", ""))
    console.print(table)

    mcp_apis = [a for a in apis if a["properties"].get("kind") == "mcp"]
    if not mcp_apis:
        raise RuntimeError("No APIs of kind 'mcp' found in API Center.")

    chosen = next((a for a in mcp_apis if a["name"] == WANTED_API_ID), mcp_apis[0])
    console.print(
        f"  picked: [green]{chosen['name']}[/green]  "
        f"({chosen['properties'].get('title')})"
    )

    deployments = _arm_get(token, f"{base}/apis/{chosen['name']}/deployments")["value"]
    if not deployments:
        raise RuntimeError(f"API '{chosen['name']}' has no deployments.")

    active = next(
        (d for d in deployments if d["properties"].get("state") == "active"),
        deployments[0],
    )
    runtime_uris = active["properties"].get("server", {}).get("runtimeUri", [])
    if not runtime_uris:
        raise RuntimeError("Active deployment has no runtimeUri.")

    runtime_uri = runtime_uris[0]
    console.print(
        f"  deployment [cyan]{active['name']}[/cyan] -> [bold]{runtime_uri}[/bold]\n"
    )
    return runtime_uri, chosen


# ---------- agent loop (mirrors Scenario B) -------------------------------


def _mcp_tools_to_openai(tools) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


async def run_agent_against(mcp_url: str) -> None:
    client = AzureOpenAI(
        azure_endpoint=GATEWAY,
        api_version=API_VERSION,
        api_key="unused",
        max_retries=0,
        default_headers={
            "Ocp-Apim-Subscription-Key": APIM_KEY,
            "x-agent-name": "mcp-registry-agent",
            "x-session-id": "lab08-registry",
        },
    )
    system = (
        "You are an underwriting assistant. Use the provided tools to answer "
        "questions about policies, guidelines, and loss runs. Always cite the "
        "tool result you used."
    )
    user_question = (
        "What are the underwriting guidelines for Cyber Liability, and is "
        "POL-002 within them?"
    )

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            console.print(
                f"  MCP connected: [green]{init.serverInfo.name}[/green] "
                f"v{init.serverInfo.version}"
            )
            mcp_tools = (await session.list_tools()).tools
            openai_tools = _mcp_tools_to_openai(mcp_tools)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_question},
            ]
            console.print(f"  [bold]user:[/bold] {user_question}")

            for hop in range(1, 6):
                resp = client.chat.completions.create(
                    model=DEPLOYMENT,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    max_tokens=400,
                )
                msg = resp.choices[0].message
                if msg.tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": msg.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in msg.tool_calls
                            ],
                        }
                    )
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        console.print(
                            f"  [yellow]hop {hop}[/yellow]  model -> tool "
                            f"[cyan]{tc.function.name}[/cyan]({args})"
                        )
                        result = await session.call_tool(tc.function.name, args)
                        out = result.content[0].text if result.content else ""
                        console.print(f"           tool -> model  {out[:140]}")
                        messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": out}
                        )
                    continue

                console.print(f"\n  [bold green]assistant:[/bold green] {msg.content}")
                return

            console.print("[red]  hit hop cap without final answer[/red]")


# ---------- entrypoint ----------------------------------------------------


async def main() -> None:
    console.rule("[bold]Demo 08C: discover MCP via Azure API Center[/bold]")
    console.print(
        Panel.fit(
            "An agent should not hard-code MCP URLs. Azure API Center acts as\n"
            "the [bold]registry[/bold]: APIs of kind 'mcp' are discoverable, the\n"
            "deployment record carries the runtime URL. We list, filter, pick,\n"
            "and connect — then run the same APIM-governed agent loop.",
            border_style="cyan",
        )
    )
    console.print(
        f"Registry: [cyan]{API_CENTER}[/cyan]  RG: [cyan]{RESOURCE_GROUP}[/cyan]"
    )
    console.print(
        f"Gateway:  [cyan]{GATEWAY}[/cyan]   Deployment: [cyan]{DEPLOYMENT}[/cyan]\n"
    )

    mcp_url, _api = discover_mcp_url()
    await run_agent_against(mcp_url)
    console.rule("[bold green]Done[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
