"""
Demo 06: Kill Switch

Three real, runnable kill-switch scenarios against the live Foundry project:

    A. Cancel an in-flight Foundry response (a long-running background call).
    B. Manually delete a specific agent VERSION via the SDK.
    C. Automated kill via Azure Monitor: POST a real alert payload at the
       deployed Logic App and watch IT call Foundry to delete the version.
       (Requires automation/deploy.ps1 to have been run first.)

Required env (loaded from demos/01-local-agent-dev/.env):
    FOUNDRY_ENDPOINT
    AZURE_OPENAI_DEPLOYMENT

Auth: AzureCliCredential (run `az login` first).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv(Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env")

console = Console()

FOUNDRY_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"]
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AGENT_NAME = os.environ.get("AGENT_NAME", "insurance-submission-agent")
KILL_VERSION_LABEL = os.environ.get("KILL_VERSION_LABEL", "kill-switch-demo")
CALLBACK_FILE = Path(__file__).parent / "automation" / ".callback_url"


def _project() -> AIProjectClient:
    return AIProjectClient(endpoint=FOUNDRY_ENDPOINT, credential=AzureCliCredential())


def _create_throwaway_version(project: AIProjectClient, label: str) -> str:
    v = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions=(
                f"This is a kill-switch demo version ({label}). "
                "It exists only to be deleted by demo_kill_switch.py."
            ),
        ),
    )
    return v.version


# ---------------------------------------------------------------------------
# A — cancel an in-flight response
# ---------------------------------------------------------------------------
def cancel_in_flight_response() -> None:
    console.print(Panel.fit(
        "[bold]A. Cancel an in-flight Foundry response[/bold]\n"
        "Start a background response, then cancel it before it finishes.",
        border_style="red",
    ))

    project = _project()
    openai_client = project.get_openai_client()

    console.print("[cyan]-> Starting background response...[/cyan]")
    resp = openai_client.responses.create(
        model=MODEL,
        input=(
            "Write a 1500-word essay on the history of marine insurance, "
            "starting from the Code of Hammurabi. Be thorough and detailed."
        ),
        background=True,
    )
    console.print(f"  response_id = [yellow]{resp.id}[/yellow]   status = {resp.status}")
    time.sleep(2)

    console.print("[cyan]-> Calling responses.cancel()...[/cyan]")
    cancelled = openai_client.responses.cancel(resp.id)
    console.print(f"  status after cancel = [bold red]{cancelled.status}[/bold red]")
    final = openai_client.responses.retrieve(resp.id)
    console.print(f"  status on retrieve  = [bold]{final.status}[/bold]")

    if final.status in {"cancelled", "canceled"}:
        console.print("[green]OK Response was cancelled - no further tokens billed.[/green]\n")
    else:
        console.print(f"[yellow]! Final status was '{final.status}' (race with completion).[/yellow]\n")


# ---------------------------------------------------------------------------
# B — manually delete an agent version via the SDK
# ---------------------------------------------------------------------------
def kill_agent_version() -> None:
    console.print(Panel.fit(
        "[bold]B. Manually delete a specific agent VERSION[/bold]\n"
        f"Create a throwaway version of [yellow]{AGENT_NAME}[/yellow], then delete it.",
        border_style="red",
    ))

    project = _project()

    try:
        project.agents.get(agent_name=AGENT_NAME)
    except ResourceNotFoundError:
        console.print(
            f"[red]Agent '{AGENT_NAME}' not found. Run demos/01-local-agent-dev/main.py first.[/red]"
        )
        return

    console.print("[cyan]-> Creating throwaway version...[/cyan]")
    version_id = _create_throwaway_version(project, f"{KILL_VERSION_LABEL}-manual")
    console.print(f"  created version [yellow]v{version_id}[/yellow]")

    console.print("[cyan]-> Calling agents.delete_version()...[/cyan]")
    project.agents.delete_version(agent_name=AGENT_NAME, agent_version=version_id)

    try:
        project.agents.get_version(agent_name=AGENT_NAME, agent_version=version_id)
        console.print(f"[red]X Version {version_id} still exists![/red]\n")
        return
    except ResourceNotFoundError:
        pass

    versions_after = sorted(v.version for v in project.agents.list_versions(agent_name=AGENT_NAME))
    console.print(f"  versions after kill: {versions_after}")
    console.print(f"[green]OK Version v{version_id} is gone. Any traffic to it now 404s.[/green]\n")


# ---------------------------------------------------------------------------
# C — automated kill via the deployed Logic App
# ---------------------------------------------------------------------------
def automated_kill_via_logic_app() -> None:
    console.print(Panel.fit(
        "[bold]C. Automated kill via Azure Monitor -> Logic App[/bold]\n"
        "POST a real common-alert-schema payload at the deployed Logic App.",
        border_style="red",
    ))

    if not CALLBACK_FILE.exists():
        console.print(
            f"[red]No Logic App callback URL found at {CALLBACK_FILE}.[/red]\n"
            "[yellow]Run automation/deploy.ps1 first to deploy the Logic App.[/yellow]\n"
            "[dim]Skipping scenario C.[/dim]\n"
        )
        return

    callback_url = CALLBACK_FILE.read_text().strip()
    console.print(f"  Logic App URL = [dim]{callback_url[:80]}...[/dim]")

    project = _project()
    console.print("[cyan]-> Creating throwaway version that the Logic App will kill...[/cyan]")
    victim_version = _create_throwaway_version(project, f"{KILL_VERSION_LABEL}-auto")
    console.print(f"  created version [yellow]v{victim_version}[/yellow]")

    alert_payload = {
        "schemaId": "azureMonitorCommonAlertSchema",
        "data": {
            "essentials": {
                "alertId": "/subscriptions/.../alertRule/agent-cost-threshold",
                "alertRule": "agent-cost-threshold",
                "severity": "Sev2",
                "signalType": "Log",
                "monitorCondition": "Fired",
                "monitoringService": "Log Analytics",
                "firedDateTime": "2026-05-13T12:00:00Z",
                "description": "Agent version exceeded cost threshold",
            },
            "alertContext": {
                "condition": {
                    "allOf": [
                        {
                            "searchQuery": "dependencies | where ...",
                            "threshold": "10",
                            "operator": "GreaterThan",
                            "metricValue": 142.7,
                            "dimensions": [
                                {"name": "agent_name", "value": AGENT_NAME},
                                {"name": "agent_version", "value": str(victim_version)},
                            ],
                        }
                    ]
                }
            },
        },
    }

    console.print("[cyan]-> POSTing alert payload at the Logic App trigger URL...[/cyan]")
    r = httpx.post(callback_url, json=alert_payload, timeout=60.0)
    console.print(f"  Logic App responded {r.status_code}: {r.text[:200]}")

    # Allow a brief moment for the workflow to finish.
    time.sleep(3)

    try:
        project.agents.get_version(agent_name=AGENT_NAME, agent_version=victim_version)
        console.print(
            f"[red]X Version v{victim_version} still exists. "
            "Check the Logic App run history in the portal.[/red]\n"
        )
        return
    except ResourceNotFoundError:
        pass

    versions_after = sorted(v.version for v in project.agents.list_versions(agent_name=AGENT_NAME))
    console.print(f"  versions after Logic App ran: {versions_after}")
    console.print(
        f"[green]OK Logic App autonomously killed v{victim_version}. "
        "In production, the App Insights cost alert posts the same payload.[/green]\n"
    )


def main() -> None:
    console.print(Panel(
        "[bold]Kill Switch Demo[/bold]\n"
        "A. Cancel in-flight response   B. Manual version delete   C. Automated kill via App Insights -> Logic App",
        title="Demo 06",
    ))

    cancel_in_flight_response()
    kill_agent_version()
    automated_kill_via_logic_app()

    console.print("[bold green]Summary[/bold green]")
    console.print("  - Cancel in-flight response  ->  openai_client.responses.cancel(id)")
    console.print("  - Manual version kill        ->  project.agents.delete_version(name, version)")
    console.print("  - Automated kill             ->  App Insights KQL alert -> action group -> Logic App -> Foundry REST DELETE")
    console.print("  - Deploy automation          ->  automation/deploy.ps1")


if __name__ == "__main__":
    main()
