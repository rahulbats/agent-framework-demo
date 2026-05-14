"""
Demo 01: Foundry-Hosted Prompt Agent (GA Agent Service)

Insurance submission processing agent built on the new GA Foundry Agent Service.
- Agent definition (model, instructions, tools) is hosted in Foundry and
  visible/editable in the portal under Build > Agents.
- Multi-turn state lives in a Foundry Conversation (no manual message list).
- Custom function tools are executed locally and the outputs returned to the
  agent via the Responses API.
"""

import json
import os
import random
from collections import defaultdict
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, Tool
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputParam,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

console = Console()

DATA_DIR = Path(__file__).parent / "data"

PROJECT_ENDPOINT = os.environ.get("FOUNDRY_ENDPOINT", "")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AGENT_NAME = os.environ.get("AGENT_NAME", "insurance-submission-agent")


# --- Document Store (loaded from data/ files) ---

_DOC_FILES = {
    "loss_run_2024.pdf": {"file": "loss_run_2024.txt", "pages": 4},
    "application_form.docx": {"file": "application_form.txt", "pages": 8},
    "coverage_summary.xlsx": {"file": "coverage_summary.txt", "pages": 2},
}

DOCUMENT_STORE = {}
for doc_name, meta in _DOC_FILES.items():
    text_path = DATA_DIR / meta["file"]
    DOCUMENT_STORE[doc_name] = {
        "filename": doc_name,
        "pages": meta["pages"],
        "ocr_text": text_path.read_text(encoding="utf-8"),
    }

GUIDELINES_DB = json.loads((DATA_DIR / "guidelines.json").read_text(encoding="utf-8"))


# --- Local tool implementations ---

def list_submission_documents() -> str:
    docs = [{"filename": d["filename"], "pages": d["pages"]} for d in DOCUMENT_STORE.values()]
    return json.dumps({"submission_id": "SUB-2026-0512-001", "documents": docs, "total": len(docs)})


def read_document(document_name: str) -> str:
    doc = DOCUMENT_STORE.get(document_name)
    if not doc:
        return json.dumps({"error": f"Document '{document_name}' not found", "available": list(DOCUMENT_STORE.keys())})
    return json.dumps({"filename": doc["filename"], "pages": doc["pages"], "content": doc["ocr_text"]})


def search_underwriting_guidelines(coverage_type: str, state: str) -> str:
    state_map = {"GEORGIA": "GA", "FLORIDA": "FL", "TEXAS": "TX", "CALIFORNIA": "CA", "NEW YORK": "NY"}
    state_upper = state_map.get(state.upper(), state.upper()[:2])
    results = [g for g in GUIDELINES_DB if state_upper in g["eligible_states"]]
    return json.dumps({"query": {"coverage_type": coverage_type, "state": state}, "results": results, "total": len(results)})


LOCAL_FUNCTIONS = {
    "list_submission_documents": lambda args: list_submission_documents(),
    "read_document": lambda args: read_document(args["document_name"]),
    "search_underwriting_guidelines": lambda args: search_underwriting_guidelines(args["coverage_type"], args["state"]),
}


# --- Tool definitions registered with the hosted agent ---

TOOLS: list[Tool] = [
    FunctionTool(
        name="list_submission_documents",
        description="List all documents in the current submission package.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        strict=True,
    ),
    FunctionTool(
        name="read_document",
        description="Read the raw OCR text content of a submission document for analysis.",
        parameters={
            "type": "object",
            "properties": {
                "document_name": {"type": "string", "description": "Filename of the document to read"},
            },
            "required": ["document_name"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="search_underwriting_guidelines",
        description="Search the underwriting guidelines database for programs matching the given coverage type and state.",
        parameters={
            "type": "object",
            "properties": {
                "coverage_type": {"type": "string", "description": "Type of coverage (e.g. 'Commercial Property')"},
                "state": {"type": "string", "description": "State (e.g. 'Georgia' or 'GA')"},
            },
            "required": ["coverage_type", "state"],
            "additionalProperties": False,
        },
        strict=True,
    ),
]

# --- Two prompt variants for A/B testing ---

INSTRUCTIONS_V1 = """You are an Insurance Submission Processing Agent.

You have tools to retrieve raw document content and guideline data. YOU perform the analysis:
1. List & read documents to get raw OCR text
2. Classify documents by analyzing their content
3. Extract key fields (insured name, limits, property details, loss history)
4. Search guidelines and evaluate whether the submission meets each program's criteria
5. Provide a recommendation with reasoning

Be precise and structured. Show your reasoning when evaluating guideline fit."""

INSTRUCTIONS_V2 = """You are a Senior Insurance Underwriter Assistant.

Work the submission like a seasoned underwriter:
- Always read every document before drawing conclusions.
- Lead with a one-line VERDICT: ACCEPT / DECLINE / REFER, then justify.
- Quote specific numbers (limits, loss amounts, TIV) and cite the document by name.
- Flag the top 3 risk concerns explicitly under a 'Red Flags' section.
- End with a 'Next Actions' checklist for the underwriter.

Be terse. No filler. Use bullet lists, not paragraphs."""

VARIANTS = {
    "v1": INSTRUCTIONS_V1,
    "v2": INSTRUCTIONS_V2,
}


def process_function_calls(openai_client, conversation_id: str, agent_name: str, agent_version: int, response):
    """If a response contains function_call items, execute them locally and submit
    the outputs back to the agent. Loop until the model returns a final text answer.
    Returns (final_response, total_tool_calls)."""
    tool_calls_total = 0
    while True:
        function_calls = [item for item in response.output if item.type == "function_call"]
        if not function_calls:
            return response, tool_calls_total

        tool_calls_total += len(function_calls)
        outputs: ResponseInputParam = []
        for call in function_calls:
            args = json.loads(call.arguments) if call.arguments else {}
            console.print(f"  [dim]→ {call.name}({json.dumps(args, separators=(',', ':'))[:60]})[/dim]")
            handler = LOCAL_FUNCTIONS.get(call.name)
            result = handler(args) if handler else json.dumps({"error": f"Unknown tool: {call.name}"})
            outputs.append(FunctionCallOutput(type="function_call_output", call_id=call.call_id, output=result))

        response = openai_client.responses.create(
            input=outputs,
            conversation=conversation_id,
            extra_body={"agent_reference": {
                "name": agent_name,
                "version": str(agent_version),
                "type": "agent_reference",
            }},
        )


def main():
    console.print(Panel(
        "[bold blue]Submission Agent[/bold blue] (Foundry Prompt Agent — GA)\n"
        "[dim]A/B test: each turn is randomly routed to V1 or V2.[/dim]\n"
        "Type [green]quit[/green] to exit (prints A/B summary), [green]clear[/green] to reset both conversations.",
        title="Agent Ready",
    ))

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    openai_client = project.get_openai_client()
    console.print(f"  [dim]Connected to Foundry: {PROJECT_ENDPOINT}[/dim]")

    # Create one agent version per variant. Foundry auto-increments the version number;
    # we pin each variant to the version it received so A/B routing is deterministic.
    variant_versions: dict[str, int] = {}
    for label, instructions in VARIANTS.items():
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=MODEL,
                instructions=instructions,
                tools=TOOLS,
            ),
        )
        variant_versions[label] = int(agent.version)
        console.print(f"  [dim]Variant {label} → {AGENT_NAME} v{agent.version}[/dim]")

    # Use one Foundry conversation per variant so each side keeps its own thread
    # of state (mixing variants in one conversation contaminates the experiment).
    conversations: dict[str, str] = {
        label: openai_client.conversations.create().id for label in VARIANTS
    }
    console.print(f"  [dim]A/B routing: 50/50 across {list(VARIANTS)}[/dim]")

    # A/B telemetry
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "tool_calls": 0})

    try:
        while True:
            try:
                user_input = console.input("\n[bold green]You:[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue
            if user_input.lower() == "quit":
                break
            if user_input.lower() == "clear":
                for label, cid in conversations.items():
                    try:
                        openai_client.conversations.delete(conversation_id=cid)
                    except Exception:
                        pass
                    conversations[label] = openai_client.conversations.create().id
                console.print("  [dim]New conversations started for both variants.[/dim]")
                continue

            # Route this turn 50/50
            variant = random.choice(list(VARIANTS))
            version_num = variant_versions[variant]
            conv_id = conversations[variant]
            console.print(f"  [dim]Routed to {variant} (v{version_num})[/dim]")

            try:
                response = openai_client.responses.create(
                    input=user_input,
                    conversation=conv_id,
                    extra_body={"agent_reference": {
                        "name": AGENT_NAME,
                        "version": str(version_num),
                        "type": "agent_reference",
                    }},
                )
                response, n_tool_calls = process_function_calls(
                    openai_client, conv_id, AGENT_NAME, version_num, response
                )
                stats[variant]["turns"] += 1
                stats[variant]["tool_calls"] += n_tool_calls
                console.print(
                    f"\n[bold blue]Agent ({variant} v{version_num}):[/bold blue] {response.output_text}"
                )
            except Exception as e:
                console.print(f"\n[bold red]Error:[/bold red] {e}")
    finally:
        # Print A/B summary
        if any(s["turns"] for s in stats.values()):
            table = Table(title="A/B Test Summary", show_header=True)
            table.add_column("Variant")
            table.add_column("Version", justify="right")
            table.add_column("Turns", justify="right")
            table.add_column("Total Tool Calls", justify="right")
            table.add_column("Avg Tool Calls / Turn", justify="right")
            for label in VARIANTS:
                s = stats[label]
                avg = (s["tool_calls"] / s["turns"]) if s["turns"] else 0
                table.add_row(label, str(variant_versions[label]), str(s["turns"]), str(s["tool_calls"]), f"{avg:.2f}")
            console.print(table)

        for cid in conversations.values():
            try:
                openai_client.conversations.delete(conversation_id=cid)
            except Exception:
                pass
        console.print("[dim]Conversations closed. Agent versions kept in Foundry — view them in the portal.[/dim]")


if __name__ == "__main__":
    main()
