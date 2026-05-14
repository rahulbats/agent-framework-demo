"""Demo: Azure Document Intelligence on a structured insurance application.

Uses the prebuilt-layout model to extract text + tables, then prebuilt-document
key-value pairs to pull named fields. Output: clean JSON the underwriting
agent can consume directly (no LLM hallucination risk).

Prereq:
    az cognitiveservices account create ... (already done by lab setup)
    Set DOCINTEL_ENDPOINT env var, or override the default below.
"""

import json
import os
import sys
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.identity import DefaultAzureCredential

ENDPOINT = os.getenv(
    "DOCINTEL_ENDPOINT",
    "https://agent-framework-docintel.cognitiveservices.azure.com",
)
DATA_FILE = Path(__file__).parent / "data" / "broker_email.txt"  # placeholder
APPLICATION_FORM = Path(__file__).parent.parent / "01-local-agent-dev" / "data" / "application_form.txt"


def analyze(path: Path) -> dict:
    cred = DefaultAzureCredential()
    client = DocumentIntelligenceClient(endpoint=ENDPOINT, credential=cred)

    print(f"\n[upload] {path.name} ({path.stat().st_size} bytes)")
    with path.open("rb") as f:
        body = f.read()

    poller = client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=AnalyzeDocumentRequest(bytes_source=body),
        content_type="application/octet-stream",
    )
    result = poller.result()

    pages = len(result.pages or [])
    tables = len(result.tables or [])
    kv_pairs = len(result.key_value_pairs or [])
    paragraphs = len(result.paragraphs or [])

    extracted_kv = {}
    for kv in result.key_value_pairs or []:
        if kv.key and kv.value:
            extracted_kv[kv.key.content.strip().rstrip(":")] = kv.value.content.strip()

    return {
        "file": path.name,
        "pages": pages,
        "tables": tables,
        "paragraphs": paragraphs,
        "key_value_pairs": kv_pairs,
        "extracted_fields": extracted_kv,
        "raw_text_excerpt": (result.content or "")[:400] + "...",
    }


if __name__ == "__main__":
    target = APPLICATION_FORM if APPLICATION_FORM.exists() else DATA_FILE
    if not target.exists():
        print(f"ERROR: file not found: {target}")
        sys.exit(1)

    out = analyze(target)
    print(json.dumps(out, indent=2))
    print("\n[note] DocIntel is best on PDFs/images. Plain .txt produces "
          "trivial layout output. For a real demo, point at a scanned ACORD PDF.")
