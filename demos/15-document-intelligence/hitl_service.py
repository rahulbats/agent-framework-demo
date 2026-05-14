"""Reference scaffolding for production HITL: notify reviewers + Entra-protected
resume endpoint.

This file is NOT wired into demo_intake_agent.py - that demo stays console-only
so the lab is runnable on a laptop. This is the shape of what you'd build when
you take the workflow to production.

Three pieces:

  1. notify_reviewers(request_id, req)
        Sends an Adaptive Card to a Teams channel (or email, or ServiceNow, ...).
        The card has a deep link back to your reviewer UI keyed on request_id.

  2. FastAPI app
        Hosts /resume/{request_id}. Protected by Entra ID:
            - validates the bearer token against your tenant's JWKS
            - requires the 'Underwriter' app role on the app registration
            - additionally checks per-request assignment (assigned_to / required_role)
              that you stamped on the parked doc when you parked it

  3. ParkedStore
        In-memory here. In prod this is Cosmos DB (or Service Bus + a dedup table).
        Park rows survive process restarts so a reviewer can resume hours later.

Run (after `pip install fastapi uvicorn pyjwt[crypto] httpx`):

    $env:AAD_TENANT_ID    = "<tenant-guid>"
    $env:AAD_AUDIENCE     = "api://<your-app-id>"
    $env:REVIEWER_ROLE    = "Underwriter"
    uvicorn hitl_service:app --reload --port 8080

Then your workflow worker calls:
    parked_store.park(request_id, ParkedDoc(...))
    await notify_reviewers(request_id, summary)

When the reviewer submits, FastAPI validates Entra, looks up the park row,
and your callback (resume_workflow) hands the responses back to the workflow.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import jwt  # PyJWT
from fastapi import Depends, FastAPI, Header, HTTPException, status
from jwt import PyJWKClient
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config (env-driven)
# ---------------------------------------------------------------------------

TENANT_ID = os.environ["AAD_TENANT_ID"]
AUDIENCE = os.environ["AAD_AUDIENCE"]                    # e.g. api://<app-id>
REVIEWER_ROLE = os.getenv("REVIEWER_ROLE", "Underwriter")
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")        # optional Teams webhook
REVIEWER_PORTAL_URL = os.getenv("REVIEWER_PORTAL_URL", "https://reviewer.example.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hitl")

_jwks_client = PyJWKClient(JWKS_URL)


# ---------------------------------------------------------------------------
# Park store - swap for Cosmos in production
# ---------------------------------------------------------------------------

@dataclass
class ParkedDoc:
    request_id: str
    doc_path: str
    fields: dict[str, Any]
    missing: list[str]
    confidence: float
    provenance: list[str]
    parked_at: float = field(default_factory=time.monotonic)

    # Per-request authorization stamps. Set these when you park, based on
    # NAICS / line of business / premium size, etc.
    assigned_to: list[str] = field(default_factory=list)        # Entra oids
    required_role: str = REVIEWER_ROLE


class ParkedStore:
    """In-memory. In production: Cosmos DB container, partitioned by request_id."""
    def __init__(self) -> None:
        self._rows: dict[str, ParkedDoc] = {}
        self._resume_cb: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def park(
        self,
        row: ParkedDoc,
        on_resume: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._rows[row.request_id] = row
        self._resume_cb[row.request_id] = on_resume
        log.info("parked request_id=%s doc=%s", row.request_id, row.doc_path)

    def get(self, request_id: str) -> ParkedDoc | None:
        return self._rows.get(request_id)

    def pop(self, request_id: str) -> tuple[ParkedDoc, Callable[[dict[str, Any]], Awaitable[None]]] | None:
        row = self._rows.pop(request_id, None)
        cb = self._resume_cb.pop(request_id, None)
        if row is None or cb is None:
            return None
        return row, cb


parked_store = ParkedStore()


# ---------------------------------------------------------------------------
# Notification - Teams Adaptive Card via Incoming Webhook
# ---------------------------------------------------------------------------

async def notify_reviewers(request_id: str, row: ParkedDoc) -> None:
    """Push an Adaptive Card so reviewers see it inline in Teams.

    For per-user @mentions and richer ACLs, replace this with a Graph call:
        POST /chats/{chatId}/messages   (delegated, on behalf of bot user)
    or use a Power Automate "Post adaptive card and wait for response" connector.
    """
    deep_link = f"{REVIEWER_PORTAL_URL}/review/{request_id}"
    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.5",
                "body": [
                    {"type": "TextBlock", "size": "Medium", "weight": "Bolder",
                     "text": f"Submission needs review: {row.doc_path}"},
                    {"type": "FactSet", "facts": [
                        {"title": "Confidence", "value": f"{row.confidence:.0%}"},
                        {"title": "Missing fields", "value": ", ".join(row.missing) or "(none)"},
                        {"title": "Pipeline", "value": " -> ".join(row.provenance)},
                        {"title": "Required role", "value": row.required_role},
                    ]},
                ],
                "actions": [
                    {"type": "Action.OpenUrl", "title": "Review in portal", "url": deep_link},
                ],
            },
        }],
    }

    if not TEAMS_WEBHOOK_URL:
        log.info("notify (stub, no webhook): %s", json.dumps(card["attachments"][0]["content"]["body"]))
        return

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(TEAMS_WEBHOOK_URL, json=card)
        r.raise_for_status()
    log.info("notified Teams for request_id=%s", request_id)


# ---------------------------------------------------------------------------
# Entra auth - validate bearer token and enforce app role
# ---------------------------------------------------------------------------

@dataclass
class CallerIdentity:
    oid: str
    upn: str
    roles: list[str]
    raw_claims: dict[str, Any]


async def require_reviewer(authorization: str = Header(...)) -> CallerIdentity:
    """FastAPI dependency. Validates the Entra bearer token and enforces the
    REVIEWER_ROLE app role. Returns the caller's identity."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}") from e

    roles = claims.get("roles") or []
    if REVIEWER_ROLE not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing role '{REVIEWER_ROLE}'")

    return CallerIdentity(
        oid=claims["oid"],
        upn=claims.get("preferred_username", claims.get("upn", "<unknown>")),
        roles=roles,
        raw_claims=claims,
    )


def _check_assignment(row: ParkedDoc, caller: CallerIdentity) -> None:
    """Per-request ACL on top of the app role. Park-time you stamped
    `assigned_to` and `required_role` on the row; enforce them here."""
    if row.required_role and row.required_role not in caller.roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This document requires role '{row.required_role}'",
        )
    if row.assigned_to and caller.oid not in row.assigned_to:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You are not assigned to this submission",
        )


# ---------------------------------------------------------------------------
# FastAPI surface
# ---------------------------------------------------------------------------

app = FastAPI(title="HITL Resume Service")


class ResumeRequest(BaseModel):
    field_overrides: dict[str, Any] = {}


class ParkedView(BaseModel):
    request_id: str
    doc_path: str
    confidence: float
    provenance: list[str]
    fields: dict[str, Any]
    missing: list[str]
    required_role: str
    assigned_to: list[str]
    parked_seconds: float


@app.get("/parked/{request_id}", response_model=ParkedView)
async def get_parked(request_id: str, caller: CallerIdentity = Depends(require_reviewer)) -> ParkedView:
    row = parked_store.get(request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    _check_assignment(row, caller)
    log.info("view by oid=%s upn=%s request_id=%s", caller.oid, caller.upn, request_id)
    return ParkedView(
        request_id=row.request_id,
        doc_path=row.doc_path,
        confidence=row.confidence,
        provenance=row.provenance,
        fields=row.fields,
        missing=row.missing,
        required_role=row.required_role,
        assigned_to=row.assigned_to,
        parked_seconds=time.monotonic() - row.parked_at,
    )


@app.post("/resume/{request_id}")
async def resume(
    request_id: str,
    body: ResumeRequest,
    caller: CallerIdentity = Depends(require_reviewer),
) -> dict[str, str]:
    popped = parked_store.pop(request_id)
    if popped is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found or already resumed")
    row, on_resume = popped
    _check_assignment(row, caller)

    # AUDIT - this is what regulators will ask for. Pipe to App Insights / Sentinel.
    log.info(
        json.dumps({
            "event": "hitl_resume",
            "request_id": request_id,
            "doc_path": row.doc_path,
            "reviewer_oid": caller.oid,
            "reviewer_upn": caller.upn,
            "parked_seconds": time.monotonic() - row.parked_at,
            "field_overrides": body.field_overrides,
        })
    )

    # Hand back to the workflow. on_resume wraps:
    #     workflow.run(responses={request_id: HumanReviewResponse(field_overrides=...)})
    await on_resume({"field_overrides": body.field_overrides})
    return {"status": "resumed", "request_id": request_id}


# ---------------------------------------------------------------------------
# Wiring sketch (pseudo-code, lives in your worker, not in this file):
# ---------------------------------------------------------------------------
#
#   async def park_callback(workflow, result, doc_path, req_event):
#       request_id = req_event.request_id
#       req: HumanReviewRequest = req_event.data
#
#       async def on_resume(payload):
#           responses = {request_id: HumanReviewResponse(**payload)}
#           new_result = await workflow.run(responses=responses)
#           # ... persist final payload, hand off to underwriting agent ...
#
#       row = ParkedDoc(
#           request_id=request_id,
#           doc_path=str(doc_path),
#           fields=req.fields,
#           missing=req.missing,
#           confidence=req.confidence,
#           provenance=req.provenance,
#           assigned_to=route_to_reviewers(req.fields),   # your routing logic
#           required_role=role_for_premium(req.fields),   # e.g. senior-underwriter for big risks
#       )
#       parked_store.park(row, on_resume)
#       await notify_reviewers(request_id, row)
#
