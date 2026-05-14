"""Provision a Content Understanding analyzer for broker submission emails.

Run once before demo_content_understanding.py / demo_intake_agent.py.

Usage:
    python setup_cu_analyzer.py            # create analyzer
    python setup_cu_analyzer.py --delete   # remove analyzer
"""

import argparse
import json
import os
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

FOUNDRY_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://rahul-agent-framework-demo.cognitiveservices.azure.com",
).rstrip("/")
ANALYZER_ID = os.getenv("CU_ANALYZER_ID", "broker-submission-email")
API_VERSION = "2025-05-01-preview"

ANALYZER_DEFINITION = {
    "description": "Extracts structured submission data from broker emails (cover letters, ACORD attachments commentary, pricing targets).",
    "scenario": "document",
    "config": {
        "returnDetails": False,
        "locales": ["en-US"],
    },
    "fieldSchema": {
        "name": "BrokerSubmissionEmail",
        "fields": {
            "broker_name": {"type": "string", "method": "extract"},
            "broker_firm": {"type": "string", "method": "extract"},
            "broker_email": {"type": "string", "method": "extract"},
            "applicant_name": {"type": "string", "method": "extract"},
            "submission_type": {
                "type": "string",
                "method": "classify",
                "enum": ["new_business", "renewal", "rewrite", "endorsement"],
            },
            "target_premium_usd": {"type": "number", "method": "extract"},
            "walk_away_premium_usd": {"type": "number", "method": "extract"},
            "decision_deadline": {"type": "date", "method": "extract"},
            "effective_date": {"type": "date", "method": "extract"},
            "competing_carriers": {"type": "integer", "method": "generate",
                                    "description": "Number of other carriers also quoting, if mentioned."},
            "risk_improvements": {
                "type": "array",
                "method": "generate",
                "description": "Loss-control or risk-mitigation upgrades the broker highlights for credit.",
                "items": {"type": "string"},
            },
            "undisclosed_claims": {
                "type": "array",
                "method": "generate",
                "description": "Claims the broker mentions that may not appear on the formal loss run.",
                "items": {"type": "string"},
            },
            "urgency": {
                "type": "string",
                "method": "classify",
                "enum": ["low", "medium", "high"],
                "description": "Based on decision deadline tightness and competitive pressure.",
            },
            "broker_sentiment_summary": {"type": "string", "method": "generate"},
        },
    },
}


def _headers() -> dict:
    cred = DefaultAzureCredential()
    token = cred.get_token("https://cognitiveservices.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_analyzer() -> None:
    url = f"{FOUNDRY_ENDPOINT}/contentunderstanding/analyzers/{ANALYZER_ID}?api-version={API_VERSION}"
    print(f"PUT {url}")
    r = requests.put(url, headers=_headers(), json=ANALYZER_DEFINITION, timeout=60)
    if r.status_code not in (200, 201, 202):
        print(f"ERROR {r.status_code}: {r.text}")
        sys.exit(1)
    op = r.headers.get("Operation-Location")
    print(f"Accepted ({r.status_code}). Polling status...")
    while op:
        time.sleep(3)
        s = requests.get(op, headers=_headers(), timeout=30)
        body = s.json()
        status = body.get("status", "unknown")
        print(f"  status={status}")
        if status.lower() in ("succeeded", "ready"):
            print(f"\n[OK] Analyzer '{ANALYZER_ID}' ready.")
            return
        if status.lower() in ("failed", "canceled"):
            print(f"[FAIL] {json.dumps(body, indent=2)}")
            sys.exit(1)


def delete_analyzer() -> None:
    url = f"{FOUNDRY_ENDPOINT}/contentunderstanding/analyzers/{ANALYZER_ID}?api-version={API_VERSION}"
    r = requests.delete(url, headers=_headers(), timeout=30)
    print(f"DELETE -> {r.status_code}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--delete", action="store_true")
    args = p.parse_args()
    if args.delete:
        delete_analyzer()
    else:
        create_analyzer()
