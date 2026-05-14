"""Demo: Confidence-routed intake WORKFLOW.

  start
    |
    v
  +---------------------+
  | DocIntelExecutor    |  prebuilt-layout extraction
  +---------------------+
        |
        +-- confidence >= HIGH --> yield_output(payload)
        |
        v (low confidence: layout-free or sparse)
  +---------------------+
  | ContentUnderstanding|  schema-driven LLM extraction
  +---------------------+
        |
        +-- confidence >= HIGH --> yield_output(payload)
        |
        v (still missing required fields)
  +---------------------+
  | HumanReviewExecutor |  ctx.request_info() - suspend/resume
  +---------------------+
        |
        v
      yield_output(payload)

Each executor:
  - extracts what it can
  - scores confidence as (populated_required_fields / total_required_fields)
  - if confidence >= threshold: emits the final normalized payload
  - else: forwards a partial payload + provenance to the next stage

This is the multi-modality fallback pattern: cheapest/most-deterministic
extractor first, escalate only when needed, ask a human last.

Prereqs:
    python setup_cu_analyzer.py     # one-time
    az login

Run:
    python demo_intake_agent.py                           # broker_email -> CU branch
    python demo_intake_agent.py --doc application_form    # form -> DI branch (high conf)
    python demo_intake_agent.py --force-hitl              # demo HITL escalation
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

import requests
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.identity import DefaultAzureCredential

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)

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
ANALYZER_ID = os.getenv("CU_ANALYZER_ID", "broker-submission-email")
CU_API_VERSION = "2025-05-01-preview"

# Required fields drive the confidence score.
REQUIRED_FIELDS = [
    "applicant_name",
    "naics",
    "annual_revenue",
    "coverage_type",
    "total_limit",
    "effective_date",
    "broker_email",
]

# Confidence threshold to short-circuit. Below this, escalate.
CONFIDENCE_THRESHOLD = 0.70

DATA_DIR = Path(__file__).parent / "data"
APPLICATION_FORM = (
    Path(__file__).parent.parent / "01-local-agent-dev" / "data" / "application_form.txt"
)
LOSS_RUN = (
    Path(__file__).parent.parent / "01-local-agent-dev" / "data" / "loss_run_2024.txt"
)
BROKER_EMAIL = DATA_DIR / "broker_email.txt"

# All known fixtures, used by --batch.
BATCH_FIXTURES: dict[str, Path] = {
    "application_form": APPLICATION_FORM,
    "broker_email": BROKER_EMAIL,
    "loss_run": LOSS_RUN,
}

cred = DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Messages flowing through the workflow
# ---------------------------------------------------------------------------

@dataclass
class IntakeRequest:
    """Initial input to the workflow."""
    document_path: str


@dataclass
class PartialExtraction:
    """Carries state between executors when confidence is too low to finalize."""
    document_path: str
    fields: dict[str, Any] = field(default_factory=dict)
    provenance: list[str] = field(default_factory=list)


@dataclass
class FinalPayload:
    """Workflow output."""
    fields: dict[str, Any]
    confidence: float
    provenance: list[str]
    required_missing: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confidence(fields: dict[str, Any]) -> tuple[float, list[str]]:
    populated = [k for k in REQUIRED_FIELDS if fields.get(k) not in (None, "", [])]
    missing = [k for k in REQUIRED_FIELDS if k not in populated]
    return (len(populated) / len(REQUIRED_FIELDS)), missing


def _normalize_di(raw_fields: dict[str, str]) -> dict[str, Any]:
    """Map DocIntel KV pairs to our canonical field names."""
    return {
        "applicant_name": raw_fields.get("Named Insured"),
        "naics": raw_fields.get("NAICS Code"),
        "annual_revenue": raw_fields.get("Annual Revenue"),
        "coverage_type": raw_fields.get("Coverage Type"),
        "total_limit": raw_fields.get("Total Limit Requested"),
        "deductible": raw_fields.get("Deductible Preferred"),
        "effective_date": raw_fields.get("Effective Date Requested"),
    }


def _normalize_cu(cu_fields: dict[str, Any]) -> dict[str, Any]:
    """Map Content Understanding analyzer fields to our canonical names."""
    return {
        "applicant_name": cu_fields.get("applicant_name"),
        "broker_name": cu_fields.get("broker_name"),
        "broker_email": cu_fields.get("broker_email"),
        "broker_firm": cu_fields.get("broker_firm"),
        "effective_date": cu_fields.get("effective_date"),
        "target_premium_usd": cu_fields.get("target_premium_usd"),
        "walk_away_premium_usd": cu_fields.get("walk_away_premium_usd"),
        "decision_deadline": cu_fields.get("decision_deadline"),
        "competing_carriers": cu_fields.get("competing_carriers"),
        "urgency": cu_fields.get("urgency"),
        "risk_improvements": cu_fields.get("risk_improvements") or [],
        "undisclosed_claims": cu_fields.get("undisclosed_claims") or [],
    }


# ---------------------------------------------------------------------------
# Executor 1 - Document Intelligence
# ---------------------------------------------------------------------------

class DocIntelExecutor(Executor):
    def __init__(self, force_hitl: bool = False) -> None:
        super().__init__(id="doc_intel")
        self.force_hitl = force_hitl

    @handler
    async def extract(
        self,
        message: IntakeRequest,
        ctx: WorkflowContext[PartialExtraction, FinalPayload],
    ) -> None:
        path = Path(message.document_path)
        print(f"\n[1/3] doc-intel  -> {path.name}")
        client = DocumentIntelligenceClient(endpoint=DOCINTEL_ENDPOINT, credential=cred)
        poller = client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=AnalyzeDocumentRequest(bytes_source=path.read_bytes()),
            content_type="application/octet-stream",
        )
        r = poller.result()
        raw = {}
        for kv in r.key_value_pairs or []:
            if kv.key and kv.value:
                raw[kv.key.content.strip().rstrip(":")] = kv.value.content.strip()
        fields = _normalize_di(raw)
        # Drop None values so they don't count as populated.
        fields = {k: v for k, v in fields.items() if v is not None}

        conf, missing = _confidence(fields)
        print(f"      confidence={conf:.0%}  missing={missing}")

        if conf >= CONFIDENCE_THRESHOLD and not self.force_hitl:
            print("      -> high confidence, finalizing")
            await ctx.yield_output(FinalPayload(
                fields=fields,
                confidence=conf,
                provenance=["doc_intelligence"],
                required_missing=missing,
            ))
            return

        # Escalate to CU.
        await ctx.send_message(PartialExtraction(
            document_path=message.document_path,
            fields=fields,
            provenance=["doc_intelligence"],
        ))


# ---------------------------------------------------------------------------
# Executor 2 - Content Understanding
# ---------------------------------------------------------------------------

class ContentUnderstandingExecutor(Executor):
    def __init__(self, force_hitl: bool = False) -> None:
        super().__init__(id="content_understanding")
        self.force_hitl = force_hitl

    @handler
    async def extract(
        self,
        message: PartialExtraction,
        ctx: WorkflowContext[PartialExtraction, FinalPayload],
    ) -> None:
        path = Path(message.document_path)
        print(f"\n[2/3] content-understanding -> {path.name}")

        token = cred.get_token("https://cognitiveservices.azure.com/.default").token
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"{FOUNDRY_ENDPOINT}/contentunderstanding/analyzers/{ANALYZER_ID}:analyze"
            f"?api-version={CU_API_VERSION}"
        )

        def _run() -> dict[str, Any]:
            r = requests.post(
                url,
                headers={**headers, "Content-Type": "application/octet-stream"},
                data=path.read_bytes(),
                timeout=60,
            )
            r.raise_for_status()
            op = r.headers["Operation-Location"]
            while True:
                time.sleep(2)
                body = requests.get(op, headers=headers, timeout=30).json()
                status = body.get("status", "").lower()
                if status == "succeeded":
                    return body
                if status in ("failed", "canceled"):
                    raise RuntimeError(json.dumps(body))

        body = await asyncio.to_thread(_run)
        fields_raw = (body.get("result", {}).get("contents") or [{}])[0].get("fields", {})
        cu_fields = {}
        for name, val in fields_raw.items():
            if isinstance(val, dict):
                for k in ("valueString", "valueNumber", "valueInteger", "valueDate",
                          "valueArray", "valueObject", "value"):
                    if k in val:
                        cu_fields[name] = val[k]
                        break
                else:
                    cu_fields[name] = val
            else:
                cu_fields[name] = val

        # Merge: prior (DI) fields win where present, CU fills gaps.
        merged = {**_normalize_cu(cu_fields), **{k: v for k, v in message.fields.items() if v}}
        merged = {k: v for k, v in merged.items() if v not in (None, "", [])}

        conf, missing = _confidence(merged)
        print(f"      confidence={conf:.0%}  missing={missing}")
        provenance = message.provenance + ["content_understanding"]

        if conf >= CONFIDENCE_THRESHOLD and not self.force_hitl:
            print("      -> high confidence, finalizing")
            await ctx.yield_output(FinalPayload(
                fields=merged,
                confidence=conf,
                provenance=provenance,
                required_missing=missing,
            ))
            return

        # Escalate to human.
        await ctx.send_message(PartialExtraction(
            document_path=message.document_path,
            fields=merged,
            provenance=provenance,
        ))


# ---------------------------------------------------------------------------
# Executor 3 - Human-in-the-loop
# ---------------------------------------------------------------------------

@dataclass
class HumanReviewRequest:
    document_path: str
    fields: dict[str, Any]
    missing: list[str]
    confidence: float
    provenance: list[str]


@dataclass
class HumanReviewResponse:
    """Outside world fills in (or confirms) the missing fields."""
    field_overrides: dict[str, Any] = field(default_factory=dict)


class HumanReviewExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="human_review")

    @handler
    async def review(
        self,
        message: PartialExtraction,
        ctx: WorkflowContext[Never, FinalPayload],
    ) -> None:
        conf, missing = _confidence(message.fields)
        print(f"\n[3/3] human-review escalation (confidence={conf:.0%})")
        await ctx.request_info(
            request_data=HumanReviewRequest(
                document_path=message.document_path,
                fields=message.fields,
                missing=missing,
                confidence=conf,
                provenance=message.provenance,
            ),
            response_type=HumanReviewResponse,
        )

    @response_handler
    async def on_human_response(
        self,
        request: HumanReviewRequest,
        response: HumanReviewResponse,
        ctx: WorkflowContext[Never, FinalPayload],
    ) -> None:
        merged = {**request.fields, **response.field_overrides}
        merged = {k: v for k, v in merged.items() if v not in (None, "", [])}
        conf, missing = _confidence(merged)
        await ctx.yield_output(FinalPayload(
            fields=merged,
            confidence=conf,
            provenance=request.provenance + ["human_review"],
            required_missing=missing,
        ))


# ---------------------------------------------------------------------------
# Console HITL implementation (swap for Teams / email / portal in production)
# ---------------------------------------------------------------------------

# Single global lock so concurrent docs don't interleave their prompts on stdin.
_HITL_LOCK = asyncio.Lock()


async def prompt_human(req: HumanReviewRequest, tag: str = "") -> HumanReviewResponse:
    async with _HITL_LOCK:
        print("\n" + "=" * 78)
        print(f"HUMAN REVIEW NEEDED  {tag}".rstrip())
        print("=" * 78)
        print(f"Document      : {Path(req.document_path).name}")
        print(f"Confidence    : {req.confidence:.0%}")
        print(f"Provenance    : {' -> '.join(req.provenance)}")
        print(f"Missing fields: {', '.join(req.missing) or '(none - forced HITL)'}")
        print("\nCurrent extracted fields:")
        print(json.dumps(req.fields, indent=2, default=str))
        print()

        overrides: dict[str, Any] = {}
        for fname in req.missing:
            val = await asyncio.to_thread(input, f"  {fname} (blank to skip): ")
            val = val.strip()
            if val:
                overrides[fname] = val
        print("=" * 78)
        return HumanReviewResponse(field_overrides=overrides)


# ---------------------------------------------------------------------------
# Wire and run the workflow
# ---------------------------------------------------------------------------

def _build_workflow(force_hitl: bool):
    """Each invocation gets a fresh workflow + fresh executors. Cheaper than it
    looks - executors only hold a bool flag - and it keeps each doc's state
    isolated."""
    di = DocIntelExecutor(force_hitl=force_hitl)
    cu = ContentUnderstandingExecutor(force_hitl=force_hitl)
    hr = HumanReviewExecutor()
    return (
        WorkflowBuilder(start_executor=di)
        .add_edge(di, cu)        # only fires when DI does send_message (not yield)
        .add_edge(cu, hr)
        .build()
    )


def _print_payload(doc_path: Path, payload: FinalPayload | None) -> None:
    print("\n" + "=" * 78)
    print(f"RESULT: {doc_path.name}")
    print("=" * 78)
    if payload is None:
        print("  (no output produced)")
        return
    print(f"Confidence : {payload.confidence:.0%}")
    print(f"Provenance : {' -> '.join(payload.provenance)}")
    print(f"Missing    : {payload.required_missing or '(none)'}")
    print(json.dumps(payload.fields, indent=2, default=str))


async def run_single(doc_path: Path, force_hitl: bool) -> int:
    print("=" * 78)
    print("Lab 15 - Confidence-routed intake workflow (single)")
    print("=" * 78)
    print(f"Document        : {doc_path.name}")
    print(f"Threshold       : {CONFIDENCE_THRESHOLD:.0%}")
    print(f"Required fields : {len(REQUIRED_FIELDS)}")
    print(f"Force HITL      : {force_hitl}")

    workflow = _build_workflow(force_hitl)
    result = await workflow.run(IntakeRequest(document_path=str(doc_path)))
    while True:
        pending = result.get_request_info_events()
        if not pending:
            break
        responses: dict[str, Any] = {}
        for ev in pending:
            req: HumanReviewRequest = ev.data
            resp = await prompt_human(req)
            responses[ev.request_id] = resp
        result = await workflow.run(responses=responses)

    outputs = result.get_outputs()
    payload = outputs[-1] if outputs else None
    _print_payload(doc_path, payload)
    return 0 if payload else 1


# ---------------------------------------------------------------------------
# Intake queue: serial DI/CU, HITL parks the doc and the worker moves on.
# ---------------------------------------------------------------------------

@dataclass
class ParkedDoc:
    """A doc that hit HITL. Holds enough state to resume after human responds."""
    doc_path: Path
    workflow: Any           # agent_framework Workflow instance
    last_result: Any        # last WorkflowRunResult, has pending request_info events
    parked_at: float        # time.monotonic() when parked, for diagnostics


async def _drive_until_pause_or_done(
    workflow: Any, first_input: IntakeRequest | None, responses: dict[str, Any] | None
) -> tuple[Any, list[Any]]:
    """Advance a workflow one step. Returns (result, pending_hitl_events).
    pending_hitl_events is empty if the workflow finished."""
    if responses is not None:
        result = await workflow.run(responses=responses)
    else:
        assert first_input is not None
        result = await workflow.run(first_input)
    pending = result.get_request_info_events()
    return result, pending


async def run_batch(paths: list[Path], force_hitl: bool) -> int:
    print("=" * 78)
    print("Lab 15 - Intake queue (serial, HITL parks)")
    print("=" * 78)
    print(f"Documents       : {len(paths)}")
    print(f"Threshold       : {CONFIDENCE_THRESHOLD:.0%}")
    print(f"Force HITL      : {force_hitl}")
    for p in paths:
        print(f"  - {p.name}")

    queue: asyncio.Queue[Path] = asyncio.Queue()
    for p in paths:
        queue.put_nowait(p)

    parked: list[ParkedDoc] = []
    completed: list[tuple[Path, FinalPayload | None]] = []

    # Phase 1: drain the queue. Each doc runs DI -> CU serially. If it hits
    # HITL, park it and grab the next doc instead of blocking on stdin.
    print("\n--- phase 1: drain intake queue ---")
    while not queue.empty():
        doc_path = await queue.get()
        print(f"\n>> dequeue {doc_path.name}  (queue depth now {queue.qsize()}, parked {len(parked)})")
        try:
            workflow = _build_workflow(force_hitl)
            result, pending = await _drive_until_pause_or_done(
                workflow, IntakeRequest(document_path=str(doc_path)), None,
            )
            if pending:
                print(f"   PARKED  ({doc_path.name}) - waiting on human, moving on")
                parked.append(ParkedDoc(
                    doc_path=doc_path,
                    workflow=workflow,
                    last_result=result,
                    parked_at=time.monotonic(),
                ))
            else:
                outputs = result.get_outputs()
                payload = outputs[-1] if outputs else None
                print(f"   DONE    ({doc_path.name})")
                completed.append((doc_path, payload))
        except Exception as e:
            print(f"   FAILED  ({doc_path.name}): {e}")
            completed.append((doc_path, None))

    # Phase 2: queue is empty. Now resolve each parked doc one at a time.
    # In production, parked docs would live in durable storage (Cosmos/Blob)
    # keyed by request_id, and a webhook/Teams reply would resume them.
    if parked:
        print(f"\n--- phase 2: resolve {len(parked)} parked doc(s) ---")
    while parked:
        item = parked.pop(0)
        waited = time.monotonic() - item.parked_at
        print(f"\n>> resume {item.doc_path.name} (parked for {waited:.1f}s)")
        result = item.last_result
        try:
            while True:
                pending = result.get_request_info_events()
                if not pending:
                    break
                responses: dict[str, Any] = {}
                for ev in pending:
                    req: HumanReviewRequest = ev.data
                    resp = await prompt_human(req, tag=f"[{item.doc_path.name}]")
                    responses[ev.request_id] = resp
                result, _ = await _drive_until_pause_or_done(
                    item.workflow, None, responses,
                )
            outputs = result.get_outputs()
            payload = outputs[-1] if outputs else None
            completed.append((item.doc_path, payload))
        except Exception as e:
            print(f"   FAILED  ({item.doc_path.name}): {e}")
            completed.append((item.doc_path, None))

    print("\n" + "#" * 78)
    print("BATCH SUMMARY")
    print("#" * 78)
    failed = 0
    for p, payload in completed:
        if payload is None:
            failed += 1
        _print_payload(p, payload)
    print("\n" + "#" * 78)
    print(f"Processed {len(completed)} | succeeded {len(completed) - failed} | failed {failed}")
    print("#" * 78)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--doc",
        choices=list(BATCH_FIXTURES.keys()),
        default="broker_email",
        help="Single-doc mode: which fixture to run through the workflow.",
    )
    p.add_argument(
        "--batch",
        action="store_true",
        help="Run ALL fixtures through a serial intake queue (HITL parks).",
    )
    p.add_argument(
        "--paths",
        nargs="+",
        type=Path,
        help="Batch mode: explicit list of file paths instead of the default fixtures.",
    )
    p.add_argument("--force-hitl", action="store_true",
                   help="Force the HITL branch regardless of confidence.")
    args = p.parse_args()

    if args.batch or args.paths:
        targets = args.paths or list(BATCH_FIXTURES.values())
        missing = [t for t in targets if not t.exists()]
        if missing:
            for m in missing:
                print(f"ERROR: missing {m}")
            sys.exit(1)
        sys.exit(asyncio.run(run_batch(targets, args.force_hitl)))

    target = BATCH_FIXTURES[args.doc]
    if not target.exists():
        print(f"ERROR: missing {target}")
        sys.exit(1)
    sys.exit(asyncio.run(run_single(target, args.force_hitl)))
