"""
Demo 04 — Agent Identity in action.

Foundry agent has one tool: read_policy_document(name).
The tool uses ClientSecretCredential to act as a dedicated Service Principal
("the Agent Identity") and reads a blob from Azure Storage.

What you see when it runs
-------------------------
- The token's `oid` claim is the SP's object ID — proving the Storage call
  was made AS the Agent Identity, not as your `az login` user.
- The blob downloads successfully because the SP holds Storage Blob Data
  Reader on the storage account (granted by setup_identity.py).

Local vs Azure
--------------
This file uses ClientSecretCredential because we're running on a laptop with
no Managed Identity available. In Azure (Container Apps, AKS, App Service,
Foundry hosted runtime), assign a dedicated Managed Identity to the compute
and replace these lines:

    from azure.identity import ClientSecretCredential
    cred = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)

with an EXPLICIT ManagedIdentityCredential pinned to the Agent Identity:

    from azure.identity import ManagedIdentityCredential
    cred = ManagedIdentityCredential(client_id=AGENT_MI_CLIENT_ID)  # user-assigned
    # or, for a system-assigned MI on the host:
    # cred = ManagedIdentityCredential()

Do NOT use `DefaultAzureCredential()` here. It's a fallback chain (env vars
-> MI -> VS Code -> Azure CLI -> ...), so depending on what's configured on
the host it may silently resolve to the developer's user, an env-var SP, or
something else entirely — the same hijack risk we already guard against for
`user_credential` below by pinning it to AzureCliCredential. Pinning to
`ManagedIdentityCredential` makes the Agent Identity explicit and fails loud
if the MI isn't actually attached.

Everything else — the Storage SDK call and the RBAC grant — is identical.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, Tool
from azure.identity import (
    AzureCliCredential,
    ClientSecretCredential,
    ManagedIdentityCredential,
)
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputParam,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

HERE = Path(__file__).parent
load_dotenv(HERE.parent / "01-local-agent-dev" / ".env")
load_dotenv(HERE / ".env", override=True)

console = Console()

# Foundry control-plane (your az login user owns this)
PROJECT_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"]
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AGENT_NAME = os.environ.get("AGENT_NAME_LAB04", "policy-reader-agent")

# Agent Identity — prefer a user-assigned Managed Identity when one is
# configured; otherwise fall back to the Service Principal from
# setup_identity.py (so this file still runs on a laptop with no MI).
AGENT_MI_CLIENT_ID = os.environ.get("AGENT_MI_CLIENT_ID")

TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]

STORAGE_ACCOUNT = os.environ["STORAGE_ACCOUNT"]
STORAGE_CONTAINER = os.environ["STORAGE_CONTAINER"]
STORAGE_BLOB = os.environ["STORAGE_BLOB"]


# ---------- the Agent Identity credential ----------
# Pinned explicitly (no DefaultAzureCredential) so the chosen identity is
# unambiguous and fails loud if misconfigured.
if AGENT_MI_CLIENT_ID:
    # Production / Azure-hosted: act as the user-assigned MI attached to
    # this compute (e.g. agent-identity-lab04-mi). The MI's client_id is
    # what binds the token to that specific identity — the IMDS endpoint
    # itself is only available inside Azure compute.
    agent_credential = ManagedIdentityCredential(client_id=AGENT_MI_CLIENT_ID)
    AGENT_IDENTITY_LABEL = f"User-assigned MI {AGENT_MI_CLIENT_ID}"
else:
    # Local dev fallback: use the SP from setup_identity.py.
    agent_credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    AGENT_IDENTITY_LABEL = f"Service Principal {CLIENT_ID}"


def _decode_jwt_payload(jwt: str) -> dict:
    payload_b64 = jwt.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def read_policy_document(name: str) -> str:
    """Read a policy doc from blob storage using the Agent Identity."""
    # Prove what identity is actually being used
    token = agent_credential.get_token("https://storage.azure.com/.default").token
    claims = _decode_jwt_payload(token)
    identity_used = {
        "appid": claims.get("appid"),
        "oid":   claims.get("oid"),
        "aud":   claims.get("aud"),
    }
    console.print(f"  [magenta]Storage token claims:[/magenta] {json.dumps(identity_used, indent=2)}")

    blob_service = BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=agent_credential,
    )
    blob = blob_service.get_blob_client(container=STORAGE_CONTAINER, blob=name)
    try:
        content = blob.download_blob().readall().decode("utf-8")
    except Exception as e:
        return json.dumps({
            "identity_used": identity_used,
            "error": f"{type(e).__name__}: {e}",
        })

    return json.dumps({
        "identity_used": identity_used,
        "blob": name,
        "content": content,
    })


LOCAL_FUNCTIONS = {
    "read_policy_document": lambda args: read_policy_document(args["name"]),
}

TOOLS: list[Tool] = [
    FunctionTool(
        name="read_policy_document",
        description=(
            "Fetch the full text of a policy document from corporate storage. "
            f"The default document is '{STORAGE_BLOB}'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": f"Blob name to fetch (e.g. '{STORAGE_BLOB}').",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        strict=True,
    ),
]

INSTRUCTIONS = """You are an underwriting assistant.

When the user asks about a policy, call read_policy_document to fetch the
document, then summarize the key fields: insured, policy number, limits,
deductible, notable exclusions, and any flagged concerns.
"""


def process_function_calls(openai_client, conv_id, agent_name, version, response):
    while True:
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response
        outputs: ResponseInputParam = []
        for call in calls:
            args = json.loads(call.arguments) if call.arguments else {}
            console.print(f"  [dim]-> {call.name}({json.dumps(args, separators=(',', ':'))})[/dim]")
            handler = LOCAL_FUNCTIONS.get(call.name)
            result = handler(args) if handler else json.dumps({"error": f"Unknown tool: {call.name}"})
            outputs.append(FunctionCallOutput(type="function_call_output", call_id=call.call_id, output=result))
        response = openai_client.responses.create(
            input=outputs,
            conversation=conv_id,
            extra_body={"agent_reference": {
                "name": agent_name, "version": str(version), "type": "agent_reference",
            }},
        )


def main() -> None:
    # Foundry control-plane uses the developer's identity (you).
    # Pinning to AzureCliCredential explicitly so the SP env vars in .env
    # (used for the Agent Identity below) don't hijack DefaultAzureCredential.
    user_credential = AzureCliCredential()
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=user_credential)
    openai_client = project.get_openai_client()

    me = _decode_jwt_payload(
        user_credential.get_token("https://management.azure.com/.default").token
    )
    console.print(Panel(
        f"[bold]Demo 04 — Agent Identity in action[/bold]\n"
        f"Foundry control-plane runs as: [cyan]{me.get('upn') or me.get('unique_name') or me.get('oid')}[/cyan]\n"
        f"Agent tool calls run as:        [magenta]{AGENT_IDENTITY_LABEL}[/magenta]\n"
        f"Storage account:                {STORAGE_ACCOUNT}",
        title="Lab 4",
    ))

    agent = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions=INSTRUCTIONS,
            tools=TOOLS,
        ),
    )
    version = int(agent.version)
    console.print(f"  [dim]Foundry agent: {AGENT_NAME} v{version}[/dim]")

    conv_id = openai_client.conversations.create().id
    console.print(f"  [dim]Conversation: {conv_id}[/dim]\n")

    prompt = f"Please summarize the policy in {STORAGE_BLOB}."
    console.print(f"[bold green]User:[/bold green] {prompt}\n")

    try:
        response = openai_client.responses.create(
            input=prompt,
            conversation=conv_id,
            extra_body={"agent_reference": {
                "name": AGENT_NAME, "version": str(version), "type": "agent_reference",
            }},
        )
        response = process_function_calls(openai_client, conv_id, AGENT_NAME, version, response)
        console.print(f"\n[bold blue]Agent:[/bold blue] {response.output_text}")

        table = Table(title="Identity audit trail", show_header=True)
        table.add_column("Operation")
        table.add_column("Acted as")
        table.add_row("Foundry: create agent version + conversation", "your `az login` user")
        table.add_row("Tool: read_policy_document -> Azure Storage", AGENT_IDENTITY_LABEL)
        console.print("\n", table)
    finally:
        try:
            openai_client.conversations.delete(conversation_id=conv_id)
        except Exception:
            pass


if __name__ == "__main__":
    main()
