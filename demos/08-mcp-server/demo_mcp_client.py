"""
Demo 08: MCP client + APIM-governed agent loop

Two scenarios:

A. Talk to the MCP server with a real MCP client (streamable-http transport).
   Lists tools, calls each one. Proves the server speaks the protocol.

B. Run a small agent loop:
       gpt-4o (via APIM, Lab 7's gateway)
         -> picks an MCP tool via OpenAI function-calling
         -> we execute the tool over MCP
         -> result is fed back to the model
         -> model produces the final answer.
   This is the same wire path Foundry / Claude / Cursor use when an agent
   consumes MCP tools, just spelled out so you can see each hop.

Prereqs:
    1. python mcp_server.py            # Lab 8's MCP server
    2. APIM gateway from Lab 7 deployed (uses APIM_GATEWAY_URL +
       APIM_SUBSCRIPTION_KEY from demos/01-local-agent-dev/.env).

Run:
    python demo_mcp_client.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import AzureOpenAI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env")
console = Console()

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8081/mcp")
GATEWAY = os.environ["APIM_GATEWAY_URL"].rstrip("/")
APIM_KEY = os.environ["APIM_SUBSCRIPTION_KEY"]
DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o")
API_VERSION = "2024-12-01-preview"


# ---------- A. real MCP client probe ---------------------------------------


async def scenario_a_mcp_probe() -> None:
    console.print(
        Panel.fit(
            "[bold]Scenario A[/bold]: open a real MCP session over streamable-http,\n"
            "list the tools the server advertises, and call each one.",
            title="A. MCP protocol probe",
            border_style="cyan",
        )
    )
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            console.print(
                f"  server: [green]{init.serverInfo.name}[/green] "
                f"v{init.serverInfo.version} (protocol {init.protocolVersion})"
            )

            tools = (await session.list_tools()).tools
            table = Table(show_header=True, header_style="bold")
            table.add_column("tool")
            table.add_column("description")
            for t in tools:
                table.add_row(t.name, (t.description or "").split("\n")[0])
            console.print(table)

            # One representative call per tool.
            samples = [
                ("get_policy", {"policy_id": "POL-001"}),
                ("search_guidelines", {"insurance_type": "Commercial Property"}),
                ("get_loss_runs", {"insured_name": "Acme Corp"}),
            ]
            for name, args in samples:
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else "(empty)"
                console.print(f"  [cyan]{name}[/cyan]({args}) -> {text}")


# ---------- B. agent loop: APIM model + MCP tools --------------------------


def _mcp_tools_to_openai(tools) -> list[dict[str, Any]]:
    """Translate MCP tool descriptors into OpenAI function-calling schema."""
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


async def scenario_b_agent_loop() -> None:
    console.print(
        Panel.fit(
            "[bold]Scenario B[/bold]: agent loop. The model is gpt-4o served\n"
            "[italic]through APIM[/italic] (Lab 7's gateway). The tools come from the\n"
            "MCP server. Each model->tool->model hop is printed.",
            title="B. APIM-governed agent uses MCP tools",
            border_style="cyan",
        )
    )

    client = AzureOpenAI(
        azure_endpoint=GATEWAY,
        api_version=API_VERSION,
        api_key="unused",  # APIM uses Ocp-Apim-Subscription-Key
        max_retries=0,
        default_headers={
            "Ocp-Apim-Subscription-Key": APIM_KEY,
            "x-agent-name": "mcp-demo-agent",
            "x-session-id": "lab08-session",
        },
    )

    system = (
        "You are an underwriting assistant. Use the provided tools to answer "
        "questions about policies, guidelines, and loss runs. Always cite the "
        "tool result you used. When done, respond concisely."
    )
    user_question = (
        "For policy POL-001, summarize the policy and tell me whether the "
        "insured has any open claims."
    )

    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            openai_tools = _mcp_tools_to_openai(mcp_tools)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_question},
            ]
            console.print(f"  [bold]user:[/bold] {user_question}")

            for hop in range(1, 6):  # safety cap
                resp = client.chat.completions.create(
                    model=DEPLOYMENT,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    max_tokens=400,
                )
                msg = resp.choices[0].message

                if msg.tool_calls:
                    # Persist the assistant turn that requested the tool calls.
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
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": out,
                            }
                        )
                    continue

                console.print(f"\n  [bold green]assistant:[/bold green] {msg.content}")
                return

            console.print("[red]  hit hop cap without final answer[/red]")


async def main() -> None:
    console.rule("[bold]Demo 08: MCP server + APIM agent loop[/bold]")
    console.print(f"MCP:     [cyan]{MCP_URL}[/cyan]")
    console.print(f"Gateway: [cyan]{GATEWAY}[/cyan]   Deployment: [cyan]{DEPLOYMENT}[/cyan]\n")
    await scenario_a_mcp_probe()
    console.print()
    await scenario_b_agent_loop()
    console.rule("[bold green]Done[/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
