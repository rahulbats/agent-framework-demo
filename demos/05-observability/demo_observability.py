"""
Demo 05 - Observability for the Lab 1 agent.

Runs a few real turns against the Lab 1 hosted agent (V1 and V2). Traces flow
to Azure Application Insights via two paths:

  1. Server-side: the Foundry project is connected to App Insights, so
     Foundry emits gen_ai.* traces for every model call and tool dispatch
     automatically. (One-time setup - see README.)

  2. Client-side: this script enables the AIProjectInstrumentor and adds
     the Azure Monitor OTel exporter so our local code (the Python script
     and tool function executions) also lands in the same App Insights.

After this script runs, open App Insights -> Logs and use the queries in
kql_queries.md to inspect the runs.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

# IMPORTANT: must be set before importing azure.ai.projects.telemetry
os.environ["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] = "true"

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, Tool
from azure.ai.projects.telemetry import AIProjectInstrumentor
from azure.identity import AzureCliCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputParam,
)
from opentelemetry import trace
from rich.console import Console
from rich.panel import Panel

# Inherit Lab 1 endpoint and any local overrides
HERE = Path(__file__).parent
load_dotenv(HERE.parent / "01-local-agent-dev" / ".env")
load_dotenv(HERE / ".env", override=True)

console = Console()

PROJECT_ENDPOINT = os.environ["FOUNDRY_ENDPOINT"]
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AGENT_NAME = os.environ.get("AGENT_NAME", "insurance-submission-agent")
APPI_CONN = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")

if not APPI_CONN:
    raise SystemExit(
        "APPLICATIONINSIGHTS_CONNECTION_STRING is not set. See README - "
        "run the one-time setup commands then export the connection string."
    )

# 1) Wire up OTel - everything emitted by AIProjectInstrumentor + our manual
#    spans will flow to App Insights via the Azure Monitor exporter.
configure_azure_monitor(connection_string=APPI_CONN)
AIProjectInstrumentor().instrument(enable_content_recording=True)
tracer = trace.get_tracer("demo05.observability")


# ---------- Lab 1 tool implementations (subset, sufficient for telemetry) ----------

DATA_DIR = HERE.parent / "01-local-agent-dev" / "data"
_DOC_FILES = {
    "loss_run_2024.pdf":      ("loss_run_2024.txt", 4),
    "application_form.docx":  ("application_form.txt", 8),
    "coverage_summary.xlsx":  ("coverage_summary.txt", 2),
}
DOCS = {
    name: {
        "filename": name,
        "pages": pages,
        "ocr_text": (DATA_DIR / fn).read_text(encoding="utf-8"),
    }
    for name, (fn, pages) in _DOC_FILES.items()
}
GUIDELINES = json.loads((DATA_DIR / "guidelines.json").read_text(encoding="utf-8"))


def list_submission_documents() -> str:
    with tracer.start_as_current_span("tool.list_submission_documents"):
        return json.dumps({
            "submission_id": "SUB-2026-0513-001",
            "documents": [{"filename": d["filename"], "pages": d["pages"]} for d in DOCS.values()],
            "total": len(DOCS),
        })


def read_document(name: str) -> str:
    with tracer.start_as_current_span("tool.read_document") as span:
        span.set_attribute("document.name", name)
        doc = DOCS.get(name)
        if not doc:
            span.set_attribute("error", True)
            return json.dumps({"error": f"Document '{name}' not found"})
        span.set_attribute("document.pages", doc["pages"])
        return json.dumps(doc)


def search_underwriting_guidelines(coverage_type: str, state: str) -> str:
    with tracer.start_as_current_span("tool.search_underwriting_guidelines") as span:
        span.set_attribute("query.coverage_type", coverage_type)
        span.set_attribute("query.state", state)
        state_map = {"GEORGIA": "GA", "FLORIDA": "FL", "TEXAS": "TX",
                     "CALIFORNIA": "CA", "NEW YORK": "NY"}
        s = state_map.get(state.upper(), state.upper()[:2])
        results = [g for g in GUIDELINES if s in g["eligible_states"]]
        span.set_attribute("results.count", len(results))
        return json.dumps({"results": results, "total": len(results)})


LOCAL_FUNCTIONS = {
    "list_submission_documents": lambda a: list_submission_documents(),
    "read_document":              lambda a: read_document(a["document_name"]),
    "search_underwriting_guidelines":
        lambda a: search_underwriting_guidelines(a["coverage_type"], a["state"]),
}

TOOLS: list[Tool] = [
    FunctionTool(
        name="list_submission_documents",
        description="List all documents in the current submission package.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        strict=True,
    ),
    FunctionTool(
        name="read_document",
        description="Read a submission document.",
        parameters={
            "type": "object",
            "properties": {"document_name": {"type": "string"}},
            "required": ["document_name"], "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="search_underwriting_guidelines",
        description="Search underwriting guidelines by coverage type and state.",
        parameters={
            "type": "object",
            "properties": {
                "coverage_type": {"type": "string"},
                "state":         {"type": "string"},
            },
            "required": ["coverage_type", "state"], "additionalProperties": False,
        },
        strict=True,
    ),
]

INSTRUCTIONS_V1 = "You are an insurance submission analyst. Use tools to read documents, then make a recommendation."
INSTRUCTIONS_V2 = "You are a senior underwriter. Lead with VERDICT (ACCEPT/DECLINE/REFER). Be terse."

VARIANTS = {"v1": INSTRUCTIONS_V1, "v2": INSTRUCTIONS_V2}


def process_function_calls(openai_client, conv_id, agent_name, version, response):
    while True:
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response
        outputs: ResponseInputParam = []
        for call in calls:
            args = json.loads(call.arguments) if call.arguments else {}
            handler = LOCAL_FUNCTIONS.get(call.name)
            result = handler(args) if handler else json.dumps({"error": f"Unknown tool: {call.name}"})
            outputs.append(FunctionCallOutput(
                type="function_call_output", call_id=call.call_id, output=result,
            ))
        response = openai_client.responses.create(
            input=outputs,
            conversation=conv_id,
            extra_body={"agent_reference": {
                "name": agent_name, "version": str(version), "type": "agent_reference",
            }},
        )


def main() -> None:
    console.print(Panel(
        "[bold]Demo 05 - Real traces from Lab 1's agent[/bold]\n"
        f"Foundry project: {PROJECT_ENDPOINT}\n"
        f"App Insights:    {APPI_CONN.split(';')[0]}",
        title="Lab 5",
    ))

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=AzureCliCredential())
    openai_client = project.get_openai_client()

    # Create one version per variant (re-uses the agent if it already exists)
    variant_versions: dict[str, int] = {}
    for label, instructions in VARIANTS.items():
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(model=MODEL, instructions=instructions, tools=TOOLS),
        )
        variant_versions[label] = int(agent.version)
        console.print(f"  [dim]{label} -> {AGENT_NAME} v{agent.version}[/dim]")

    prompts = [
        "List the submission documents and tell me what's in the loss run.",
        "Is a $10M Commercial Property risk in Georgia eligible?",
        "Read the application form and summarize the insured.",
        "What's the verdict on this submission?",
    ]

    # One conversation per variant so context isn't shared between A/B
    convs: dict[str, str] = {label: openai_client.conversations.create().id for label in VARIANTS}

    try:
        for i, prompt in enumerate(prompts, start=1):
            label = random.choice(list(VARIANTS))
            version = variant_versions[label]
            with tracer.start_as_current_span("demo.turn") as span:
                span.set_attribute("agent.name", AGENT_NAME)
                span.set_attribute("agent.version", version)
                span.set_attribute("variant", label)
                span.set_attribute("turn", i)
                span.set_attribute("prompt", prompt)

                console.print(f"\n[bold green]Turn {i} ({label} v{version}):[/bold green] {prompt}")
                response = openai_client.responses.create(
                    input=prompt,
                    conversation=convs[label],
                    extra_body={"agent_reference": {
                        "name": AGENT_NAME, "version": str(version), "type": "agent_reference",
                    }},
                )
                response = process_function_calls(
                    openai_client, convs[label], AGENT_NAME, version, response,
                )
                final = response.output_text or ""
                span.set_attribute("response.length", len(final))
                console.print(f"[blue]{final[:240]}{'...' if len(final) > 240 else ''}[/blue]")
    finally:
        for cid in convs.values():
            try:
                openai_client.conversations.delete(conversation_id=cid)
            except Exception:
                pass

    console.print(Panel(
        "Done. Traces should appear in App Insights within ~1-2 minutes.\n\n"
        "Open App Insights -> Logs and try queries from [bold]kql_queries.md[/bold]:\n"
        "  - Recent agent turns (table 'dependencies')\n"
        "  - Token usage and cost per turn\n"
        "  - Tool call latency P50/P95\n"
        "  - V1 vs V2 comparison",
        title="Next",
    ))


if __name__ == "__main__":
    main()
