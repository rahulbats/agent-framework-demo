"""Demo: Azure AI Content Understanding on a broker submission email.

Uses the 'broker-submission-email' analyzer (created by setup_cu_analyzer.py)
to extract structured fields from unstructured prose using a NL-defined schema.
Returns schema-typed JSON that the underwriting agent can consume directly.

Contrast with DocIntel:
  - DocIntel = layout + key-value extraction from forms (deterministic, layout-driven)
  - Content Understanding = LLM-reasoned extraction across modalities (schema-driven)

Prereq:
    python setup_cu_analyzer.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

FOUNDRY_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://rahul-agent-framework-demo.cognitiveservices.azure.com",
).rstrip("/")
ANALYZER_ID = os.getenv("CU_ANALYZER_ID", "broker-submission-email")
API_VERSION = "2025-05-01-preview"

EMAIL_FILE = Path(__file__).parent / "data" / "broker_email.txt"


def _headers(content_type: str = "application/json") -> dict:
    cred = DefaultAzureCredential()
    token = cred.get_token("https://cognitiveservices.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": content_type}


def analyze(path: Path) -> dict:
    url = (
        f"{FOUNDRY_ENDPOINT}/contentunderstanding/analyzers/{ANALYZER_ID}:analyze"
        f"?api-version={API_VERSION}"
    )
    print(f"[POST] {url}")
    with path.open("rb") as f:
        body = f.read()

    r = requests.post(
        url, headers=_headers("application/octet-stream"), data=body, timeout=60
    )
    if r.status_code != 202:
        print(f"ERROR {r.status_code}: {r.text}")
        sys.exit(1)

    op = r.headers["Operation-Location"]
    print("[poll] waiting for analysis...")
    while True:
        time.sleep(2)
        s = requests.get(op, headers=_headers(), timeout=30)
        body = s.json()
        status = body.get("status", "unknown").lower()
        print(f"  status={status}")
        if status == "succeeded":
            return body.get("result", body)
        if status in ("failed", "canceled"):
            print(json.dumps(body, indent=2))
            sys.exit(1)


def normalize_fields(result: dict) -> dict:
    """CU returns fields nested under contents[0].fields with type-tagged values."""
    contents = result.get("contents", [])
    if not contents:
        return {}
    fields = contents[0].get("fields", {})
    out = {}
    for name, val in fields.items():
        if not isinstance(val, dict):
            out[name] = val
            continue
        # Pick the first non-metadata value key.
        for k in ("valueString", "valueNumber", "valueInteger", "valueDate",
                  "valueArray", "valueObject", "value"):
            if k in val:
                out[name] = val[k]
                break
        else:
            out[name] = val
    return out


if __name__ == "__main__":
    if not EMAIL_FILE.exists():
        print(f"ERROR: {EMAIL_FILE} not found")
        sys.exit(1)

    raw = analyze(EMAIL_FILE)
    structured = normalize_fields(raw)
    print("\n=== Structured Submission Metadata ===")
    print(json.dumps(structured, indent=2, default=str))
