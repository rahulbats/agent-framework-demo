"""
Demo 03: Session-Level Isolation (Foundry Conversations)

Uses the `insurance-submission-agent` from Lab 01 to prove that two parallel
Foundry Conversations against the SAME agent share zero state — even when one
talks to v1 and the other to v2.

We:
  1. Look up the agent's latest two versions (v1 = the analytical variant,
     v2 = the senior-underwriter variant, both created by Lab 01).
  2. Open two independent Conversations (S1, S2) — S1 pinned to v1, S2 to v2.
  3. Inject distinct broker PII into each session (Acme Corp vs Beta Industries).
  4. Ask each session to recall its OWN data. If Foundry leaked state across
     conversations the model would surface the other side's company. It doesn't.
"""

import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Pull config from Lab 01's .env so we reuse the same project + agent
load_dotenv(Path(__file__).parent.parent / "01-local-agent-dev" / ".env")
load_dotenv()  # let a local .env override if present

console = Console()

PROJECT_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "insurance-submission-agent")


def ask(openai_client, conversation_id: str, agent_name: str, version: int, prompt: str) -> str:
    response = openai_client.responses.create(
        input=prompt,
        conversation=conversation_id,
        extra_body={"agent_reference": {
            "name": agent_name,
            "version": str(version),
            "type": "agent_reference",
        }},
    )
    # We don't want this demo to fight with tool calls — just take whatever text
    # the model returns. The agent will answer recall questions without needing
    # the submission tools.
    return response.output_text or "(no text response)"


def get_latest_two_versions(project: AIProjectClient, agent_name: str) -> tuple[int, int]:
    versions = sorted(
        (int(v.version) for v in project.agents.list_versions(agent_name=agent_name)),
        reverse=True,
    )
    if len(versions) < 2:
        raise RuntimeError(
            f"Agent '{agent_name}' has fewer than 2 versions. "
            "Run Lab 01 (`python ../01-local-agent-dev/main.py`) once to create v1 + v2."
        )
    # The most recent two versions correspond to the v1 + v2 variants Lab 01
    # creates on each run. Return them as (older, newer) for stable labelling.
    newer, older = versions[0], versions[1]
    return older, newer


def main():
    console.print(Panel(
        "[bold]Session-Level Isolation[/bold] (Foundry Conversations)\n"
        f"Agent: [cyan]{AGENT_NAME}[/cyan]",
        title="Demo 03",
    ))

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    openai_client = project.get_openai_client()

    v1, v2 = get_latest_two_versions(project, AGENT_NAME)
    console.print(f"  [dim]Using {AGENT_NAME} v{v1} (S1) and v{v2} (S2)[/dim]")

    s1 = openai_client.conversations.create().id
    s2 = openai_client.conversations.create().id
    console.print(f"  [dim]S1 conversation: {s1}[/dim]")
    console.print(f"  [dim]S2 conversation: {s2}[/dim]")

    try:
        # --- Step 1: inject distinct PII into each session ---
        console.print("\n[bold cyan]Step 1: Send distinct broker data to each session[/bold cyan]")

        s1_intro = ask(openai_client, s1, AGENT_NAME, v1,
            "Please note these submission details for our conversation: "
            "company Acme Corp, $10M commercial property coverage, warehouse in Georgia. "
            "Briefly confirm you have noted them."
        )
        console.print(f"  [dim]S1 (v{v1}):[/dim] {s1_intro[:200]}")

        s2_intro = ask(openai_client, s2, AGENT_NAME, v2,
            "Please note these submission details for our conversation: "
            "company Beta Industries, $5M cyber liability coverage, operations in Texas. "
            "Briefly confirm you have noted them."
        )
        console.print(f"  [dim]S2 (v{v2}):[/dim] {s2_intro[:200]}")

        # --- Step 2: multi-turn recall within each session ---
        console.print("\n[bold cyan]Step 2: Ask each session to recall its own data[/bold cyan]")

        recall_prompt = (
            "From our conversation so far, please restate the company name, "
            "coverage amount, coverage type, and state. If any detail was not "
            "shared in this conversation, say 'unknown' for that field."
        )

        s1_recall = ask(openai_client, s1, AGENT_NAME, v1, recall_prompt)
        console.print(f"\n  [green]S1 recall:[/green] {s1_recall}")

        s2_recall = ask(openai_client, s2, AGENT_NAME, v2, recall_prompt)
        console.print(f"\n  [green]S2 recall:[/green] {s2_recall}")

        # --- Step 3: verification ---
        console.print("\n[bold cyan]Step 3: Verify no cross-session leakage[/bold cyan]")

        s1_lower = s1_recall.lower()
        s2_lower = s2_recall.lower()

        checks = [
            ("S1 remembers 'Acme Corp'",        "acme" in s1_lower,        True),
            ("S1 does NOT mention 'Beta'",      "beta" not in s1_lower,    True),
            ("S1 does NOT mention 'Texas'",     "texas" not in s1_lower,   True),
            ("S2 remembers 'Beta Industries'",  "beta" in s2_lower,        True),
            ("S2 does NOT mention 'Acme'",      "acme" not in s2_lower,    True),
            ("S2 does NOT mention 'Georgia'",   "georgia" not in s2_lower, True),
        ]

        table = Table(title="Session Isolation Verification")
        table.add_column("Check", style="cyan")
        table.add_column("Status", justify="center")

        all_pass = True
        for label, actual, expected in checks:
            ok = (actual == expected)
            all_pass &= ok
            table.add_row(label, "[green]PASS[/green]" if ok else "[red]FAIL — LEAK[/red]")

        console.print(table)

        if all_pass:
            console.print(
                "\n[bold green]Session isolation verified.[/bold green] "
                "Two Foundry Conversations against the same agent share zero state."
            )
        else:
            console.print(
                "\n[bold red]Session isolation FAILED.[/bold red] "
                "Cross-session data was observed. Investigate before going to production."
            )
    finally:
        for cid in (s1, s2):
            try:
                openai_client.conversations.delete(conversation_id=cid)
            except Exception:
                pass


if __name__ == "__main__":
    main()
