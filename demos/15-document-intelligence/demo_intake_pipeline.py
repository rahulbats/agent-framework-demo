"""Demo: Intake pipeline that turns a raw broker submission into normalized JSON.

This is the production pattern: deterministic extraction tools first
(DocIntel + Content Understanding), then hand the clean JSON to the
underwriting agent. The agent never sees raw OCR — that keeps token cost
predictable and reasoning focused on the underwriting decision.

Pipeline:
    application_form  -> Document Intelligence -> form_fields
    broker_email      -> Content Understanding -> submission_metadata
    merge             -> normalized payload
    -> hand to insurance-submission-agent (Foundry-hosted) for the decision

Prereq:
    python setup_cu_analyzer.py     # one-time
    az login
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DOCINTEL_ENDPOINT = os.getenv(
    "DOCINTEL_ENDPOINT",
    "https://agent-framework-docintel.cognitiveservices.azure.com",
).rstrip("/")
FOUNDRY_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://rahul-agent-framework-demo.cognitiveservices.azure.com",
).rstrip("/")
PROJECT_ENDPOINT = os.getenv(
    "PROJECT_ENDPOINT",
    "https://rahul-agent-framework-demo.services.ai.azure.com/api/projects/rahul-agent-framework-project",
)
ANALYZER_ID = os.getenv("CU_ANALYZER_ID", "broker-submission-email")
AGENT_NAME = os.getenv("AGENT_NAME", "insurance-submission-agent")
CU_API_VERSION = "2025-05-01-preview"

DATA_DIR = Path(__file__).parent / "data"
APPLICATION_FORM = (
    Path(__file__).parent.parent / "01-local-agent-dev" / "data" / "application_form.txt"
)
BROKER_EMAIL = DATA_DIR / "broker_email.txt"

cred = DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Step 1: Document Intelligence on the application form
# ---------------------------------------------------------------------------

def extract_application_form(path: Path) -> dict:
    print(f"[docintel] analyzing {path.name}...")
    client = DocumentIntelligenceClient(endpoint=DOCINTEL_ENDPOINT, credential=cred)
    body = path.read_bytes()
    poller = client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=AnalyzeDocumentRequest(bytes_source=body),
        content_type="application/octet-stream",
    )
    r = poller.result()
    fields = {}
    for kv in r.key_value_pairs or []:
        if kv.key and kv.value:
            fields[kv.key.content.strip().rstrip(":")] = kv.value.content.strip()
    return {
        "source": path.name,
        "page_count": len(r.pages or []),
        "table_count": len(r.tables or []),
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Step 2: Content Understanding on the broker email
# ---------------------------------------------------------------------------

def extract_broker_email(path: Path) -> dict:
    print(f"[content-understanding] analyzing {path.name}...")
    token = cred.get_token("https://cognitiveservices.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{FOUNDRY_ENDPOINT}/contentunderstanding/analyzers/{ANALYZER_ID}:analyze"
        f"?api-version={CU_API_VERSION}"
    )
    r = requests.post(
        url, headers={**headers, "Content-Type": "application/octet-stream"},
        data=path.read_bytes(), timeout=60,
    )
    if r.status_code != 202:
        raise RuntimeError(f"CU analyze failed {r.status_code}: {r.text}")
    op = r.headers["Operation-Location"]
    while True:
        time.sleep(2)
        s = requests.get(op, headers=headers, timeout=30)
        body = s.json()
        status = body.get("status", "").lower()
        if status == "succeeded":
            break
        if status in ("failed", "canceled"):
            raise RuntimeError(json.dumps(body))
    fields_raw = (body.get("result", {}).get("contents") or [{}])[0].get("fields", {})
    fields = {}
    for name, val in fields_raw.items():
        if isinstance(val, dict):
            for k in ("valueString", "valueNumber", "valueInteger", "valueDate",
                      "valueArray", "valueObject", "value"):
                if k in val:
                    fields[name] = val[k]
                    break
            else:
                fields[name] = val
        else:
            fields[name] = val
    return {"source": path.name, "fields": fields}


# ---------------------------------------------------------------------------
# Step 3: Normalize into the schema the underwriting agent expects
# ---------------------------------------------------------------------------

def normalize(form: dict, email: dict) -> dict:
    f = form["fields"]
    e = email["fields"]
    return {
        "submission_id": f"SUB-{int(time.time())}",
        "applicant": {
            "name": f.get("Named Insured") or e.get("applicant_name"),
            "naics": f.get("NAICS Code"),
            "annual_revenue_usd": f.get("Annual Revenue"),
            "state": (f.get("Mailing Address") or "").split(",")[-1].strip()[:2],
        },
        "coverage": {
            "type": f.get("Coverage Type"),
            "total_limit_usd": f.get("Total Limit Requested"),
            "deductible_usd": f.get("Deductible Preferred"),
            "effective_date": f.get("Effective Date Requested") or e.get("effective_date"),
        },
        "broker": {
            "name": e.get("broker_name"),
            "firm": e.get("broker_firm"),
            "email": e.get("broker_email"),
        },
        "commercial_terms": {
            "target_premium_usd": e.get("target_premium_usd"),
            "walk_away_premium_usd": e.get("walk_away_premium_usd"),
            "decision_deadline": e.get("decision_deadline"),
            "competing_carriers": e.get("competing_carriers"),
            "urgency": e.get("urgency"),
        },
        "risk_signals": {
            "improvements_for_credit": e.get("risk_improvements") or [],
            "undisclosed_claims": e.get("undisclosed_claims") or [],
        },
        "_provenance": {
            "form_source": form["source"],
            "email_source": email["source"],
            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


# ---------------------------------------------------------------------------
# Step 4: Hand to underwriting agent
# ---------------------------------------------------------------------------

def underwrite(payload: dict) -> str:
    print(f"[agent] handing payload to {AGENT_NAME}...")
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred)
    conv = project.agents.create_conversation(agent_name=AGENT_NAME)
    msg = (
        "You are receiving a normalized submission payload from the intake pipeline. "
        "All fields have been pre-extracted; do not call list_submission_documents. "
        "Make an underwriting decision and respond with VERDICT + rationale.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )
    response = project.agents.run(
        agent_name=AGENT_NAME,
        conversation_id=conv.id,
        input=[{"role": "user", "content": msg}],
    )
    return response.output_text if hasattr(response, "output_text") else str(response)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not APPLICATION_FORM.exists() or not BROKER_EMAIL.exists():
        print("ERROR: missing source files")
        sys.exit(1)

    form = extract_application_form(APPLICATION_FORM)
    email = extract_broker_email(BROKER_EMAIL)
    payload = normalize(form, email)

    print("\n=== Normalized Submission Payload ===")
    print(json.dumps(payload, indent=2, default=str))

    if "--no-agent" in sys.argv:
        sys.exit(0)

    print("\n=== Underwriting Decision ===")
    print(underwrite(payload))
