"""
Containerized insurance-submission agent.

Designed to run inside Azure Container Apps as a self-hosted agent (the agent
loop lives in this process; it calls Azure OpenAI directly). Exposes a tiny
HTTP API: GET /health and POST /invoke.

Identity:
  - Locally:  AZURE_OPENAI_TOKEN env var (e.g. `az account get-access-token`)
  - In ACA:   user-assigned managed identity (set AZURE_CLIENT_ID to its clientId)
"""

import json
import logging
import os
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
from openai import AzureOpenAI

from app.config import AgentConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"


# --- Document Store (loaded from data/ files) ---
# In production: Azure Blob Storage + Document Intelligence OCR

_DOC_FILES = {
    "loss_run_2024.pdf": {"file": "loss_run_2024.txt", "pages": 4},
    "application_form.docx": {"file": "application_form.txt", "pages": 8},
    "coverage_summary.xlsx": {"file": "coverage_summary.txt", "pages": 2},
}

DOCUMENT_STORE = {}
for _doc_name, _meta in _DOC_FILES.items():
    _text_path = DATA_DIR / _meta["file"]
    DOCUMENT_STORE[_doc_name] = {
        "filename": _doc_name,
        "pages": _meta["pages"],
        "ocr_text": _text_path.read_text(encoding="utf-8"),
    }


# --- Guidelines Database (loaded from data/guidelines.json) ---

GUIDELINES_DB = json.loads((DATA_DIR / "guidelines.json").read_text(encoding="utf-8"))


def read_document(document_name: str) -> str:
    """Retrieve raw OCR text content of a submission document."""
    doc = DOCUMENT_STORE.get(document_name)
    if not doc:
        return json.dumps({"error": f"Document '{document_name}' not found", "available": list(DOCUMENT_STORE.keys())})
    return json.dumps({"filename": doc["filename"], "pages": doc["pages"], "content": doc["ocr_text"]})


def list_submission_documents() -> str:
    """List all documents in the current submission package."""
    docs = [{"filename": d["filename"], "pages": d["pages"]} for d in DOCUMENT_STORE.values()]
    return json.dumps({"submission_id": "SUB-2026-0512-001", "documents": docs, "total": len(docs)})


def search_underwriting_guidelines(coverage_type: str, state: str) -> str:
    """Search the underwriting guidelines database for matching programs."""
    state_map = {"GEORGIA": "GA", "FLORIDA": "FL", "TEXAS": "TX", "CALIFORNIA": "CA", "NEW YORK": "NY"}
    state_upper = state_map.get(state.upper(), state.upper()[:2])
    results = [g for g in GUIDELINES_DB if state_upper in g["eligible_states"]]
    return json.dumps({"query": {"coverage_type": coverage_type, "state": state}, "results": results, "total": len(results)})


# --- Tool definitions for OpenAI function calling ---

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_submission_documents",
            "description": "List all documents in the current submission package.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read the raw OCR text content of a submission document for analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_name": {"type": "string", "description": "Filename of the document to read"},
                },
                "required": ["document_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_underwriting_guidelines",
            "description": "Search the underwriting guidelines database for programs matching the given coverage type and state. Returns raw guideline records for the agent to evaluate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "coverage_type": {"type": "string", "description": "Type of coverage (e.g. 'Commercial Property')"},
                    "state": {"type": "string", "description": "State (e.g. 'Georgia' or 'GA')"},
                },
                "required": ["coverage_type", "state"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "list_submission_documents": lambda args: list_submission_documents(),
    "read_document": lambda args: read_document(args["document_name"]),
    "search_underwriting_guidelines": lambda args: search_underwriting_guidelines(args["coverage_type"], args["state"]),
}

SYSTEM_PROMPT = f"""You are an Insurance Submission Processing Agent (Version: {AgentConfig.AGENT_VERSION}).

You have tools to retrieve raw document content and guideline data. YOU perform the analysis:
1. List & read documents to get raw OCR text
2. Classify documents by analyzing their content
3. Extract key fields (insured name, limits, property details, loss history)
4. Search guidelines and evaluate whether the submission meets each program's criteria
5. Provide a recommendation with reasoning

Be precise and structured. Show your reasoning when evaluating guideline fit."""


# --- Agent Loop ---

def run_agent_loop(client: AzureOpenAI, messages: list) -> str:
    """Run the agent loop — call LLM, execute tools, repeat until done."""
    while True:
        response = client.chat.completions.create(
            model=AgentConfig.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        message = response.choices[0].message
        messages.append(message)

        if not message.tool_calls:
            return message.content or ""

        for tool_call in message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            logger.info(f"Tool call: {fn_name}")
            result = TOOL_FUNCTIONS[fn_name](fn_args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })


# --- HTTP Server ---

client: AzureOpenAI = None


class AgentHandler(BaseHTTPRequestHandler):
    # ACA's Envoy ingress expects HTTP/1.1 with explicit Content-Length.
    protocol_version = "HTTP/1.1"

    def _write_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._write_json(200, {
                "status": "healthy",
                "version": AgentConfig.AGENT_VERSION,
            })
        else:
            self._write_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/invoke":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            user_input = body.get("input", "")
            session_id = body.get("session_id", "default")

            logger.info(f"Session {session_id}: {user_input[:100]}...")

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ]

            output = run_agent_loop(client, messages)

            self._write_json(200, {
                "output": output,
                "session_id": session_id,
                "agent_version": AgentConfig.AGENT_VERSION,
            })
        else:
            self._write_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        logger.info(format, *args)


def main():
    global client

    # For local Docker testing: pass AZURE_OPENAI_TOKEN from `az account get-access-token`
    # In production (Foundry/ACA): DefaultAzureCredential uses managed identity automatically
    static_token = os.environ.get("AZURE_OPENAI_TOKEN")
    uami_client_id = os.environ.get("AZURE_CLIENT_ID")
    if static_token:
        logger.info("Auth: static token from AZURE_OPENAI_TOKEN")
        client = AzureOpenAI(
            azure_endpoint=AgentConfig.AZURE_OPENAI_ENDPOINT,
            azure_ad_token=static_token,
            api_version=AgentConfig.AZURE_OPENAI_API_VERSION,
        )
    else:
        if uami_client_id:
            logger.info(f"Auth: user-assigned managed identity {uami_client_id}")
            credential = ManagedIdentityCredential(client_id=uami_client_id)
        else:
            logger.info("Auth: DefaultAzureCredential")
            credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(
            azure_endpoint=AgentConfig.AZURE_OPENAI_ENDPOINT,
            azure_ad_token_provider=token_provider,
            api_version=AgentConfig.AZURE_OPENAI_API_VERSION,
        )

    server = ThreadingHTTPServer(("0.0.0.0", AgentConfig.AGENT_PORT), AgentHandler)
    logger.info(f"Agent {AgentConfig.AGENT_VERSION} listening on port {AgentConfig.AGENT_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
