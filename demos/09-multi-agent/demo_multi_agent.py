"""
Lab 9 — Multi-agent submission triage with human-in-the-loop approval.

Real Microsoft Agent Framework (`agent_framework` v1.3+) workflow:

    classifier  ->  extractor  ->  matcher  ->  human_approval

* Each LLM call is routed through the APIM AI Gateway from Lab 7
  (token metering, content safety, key isolation).
* `extractor` and `matcher` consume the MCP server from Lab 8 via
  `MCPStreamableHTTPTool` (real `get_policy` / `search_guidelines` /
  `get_loss_runs` tools).
* `human_approval` is a custom `Executor` that pauses the workflow,
  shows the matcher's recommendation to a human underwriter, and only
  emits the final workflow output after the human approves, edits, or
  rejects it.

Prereqs
-------
1. Lab 7 APIM gateway deployed (env: APIM_GATEWAY_URL, APIM_SUBSCRIPTION_KEY).
2. Lab 8 MCP server running locally:
       python demos/08-mcp-server/mcp_server.py
3. Packages: `pip install agent-framework` (>=1.3).
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

from dotenv import load_dotenv

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    MCPStreamableHTTPTool,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from agent_framework._types import Message
from agent_framework.openai import OpenAIChatCompletionClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env"
load_dotenv(ENV_FILE)

APIM_URL = os.environ["APIM_GATEWAY_URL"].rstrip("/")
APIM_KEY = os.environ["APIM_SUBSCRIPTION_KEY"]
DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o")
MCP_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8081/mcp")
API_VERSION = "2024-08-01-preview"

# A sample inbound submission (the kind a broker emails in).
SUBMISSION_TEXT = """
From: broker@acme-insurance.com
Subject: New submission — Acme Manufacturing Corp

Insured:           Acme Manufacturing Corp
Industry:          Light manufacturing (metal fabrication)
Requested lines:   General Liability ($2M / $4M), Cyber Liability ($1M),
                   Property ($5M building, $2M contents)
Effective date:    2025-01-01
Loss history:      See attached loss_run_2024 — 2 GL claims, 1 cyber incident
Policy ID on file: POL-78421
""".strip()


# ---------------------------------------------------------------------------
# Chat client factory — every call goes through APIM
# ---------------------------------------------------------------------------

def make_apim_client(agent_name: str) -> OpenAIChatCompletionClient:
    """`OpenAIChatCompletionClient` configured to hit APIM as if it were Azure OpenAI."""
    return OpenAIChatCompletionClient(
        model=DEPLOYMENT,            # APIM forwards to this deployment
        azure_endpoint=APIM_URL,     # APIM gateway URL
        api_version=API_VERSION,
        api_key="apim",              # APIM ignores this; it checks the header
        default_headers={
            "Ocp-Apim-Subscription-Key": APIM_KEY,
            "x-agent-name": f"lab09-{agent_name}",
        },
    )


# ---------------------------------------------------------------------------
# Build agents
# ---------------------------------------------------------------------------

CLASSIFIER_INSTRUCTIONS = """
You are the CLASSIFIER agent in an insurance submission pipeline.
Read the inbound broker email and produce a short, structured classification:

  - insured_name
  - industry
  - lines_of_business: list (e.g. "General Liability", "Cyber Liability", "Property")
  - urgency: low | normal | high
  - one-sentence summary

Respond as compact JSON only, no prose.
""".strip()

EXTRACTOR_INSTRUCTIONS = f"""
You are the EXTRACTOR agent. Use the MCP tools available to you to enrich
the submission with system-of-record data:

  - call `get_policy` for any policy id mentioned (e.g. POL-78421)
  - call `get_loss_runs` for the insured name
  - call `search_guidelines` for each line of business

Then return compact JSON with fields:
  policy, loss_runs, guidelines (a dict keyed by line of business).
No prose; JSON only.
""".strip()

MATCHER_INSTRUCTIONS = """
You are the MATCHER agent. You receive the classifier's output and the
extractor's enriched data in the conversation history. Decide:

  - recommended_action: one of "auto_approve", "refer_to_underwriter", "decline"
  - rationale: 2-3 sentences, citing specific guideline rules and loss history
  - flags: list of risk flags (e.g. "high_loss_frequency", "cyber_incident_2024")
  - proposed_terms: short text (premium band, deductibles, exclusions)

Respond as compact JSON only.
""".strip()


def build_agents() -> tuple[Agent, Agent, Agent, MCPStreamableHTTPTool]:
    mcp_tool = MCPStreamableHTTPTool(
        name="underwriting",
        url=MCP_URL,
        request_timeout=30,
    )

    classifier = Agent(
        client=make_apim_client("classifier"),
        instructions=CLASSIFIER_INSTRUCTIONS,
        name="classifier",
    )
    extractor = Agent(
        client=make_apim_client("extractor"),
        instructions=EXTRACTOR_INSTRUCTIONS,
        name="extractor",
        tools=[mcp_tool],
    )
    matcher = Agent(
        client=make_apim_client("matcher"),
        instructions=MATCHER_INSTRUCTIONS,
        name="matcher",
        tools=[mcp_tool],
    )
    return classifier, extractor, matcher, mcp_tool


# ---------------------------------------------------------------------------
# Human-in-the-loop executor (real suspend/resume via ctx.request_info)
# ---------------------------------------------------------------------------

@dataclass
class ApprovalRequest:
    """What the workflow asks the outside world for."""
    recommendation: str
    upstream_messages: list[Any] = field(default_factory=list)


@dataclass
class HumanDecision:
    """What the outside world sends back to resume the workflow."""
    decision: str           # "approved" | "rejected" | "edited"
    recommendation: str = ""  # optional override (used for "edited")
    comment: str = ""


@dataclass
class FinalDecision:
    """Final workflow output emitted after human approval."""
    decision: str
    recommendation: str
    human_comment: str = ""
    upstream_messages: list[Any] = field(default_factory=list)


class HumanApprovalExecutor(Executor):
    """
    Suspends the workflow via `ctx.request_info(...)`. The host application
    receives an `ApprovalRequest` (as a `request_info` WorkflowEvent), notifies
    a human through whatever channel it likes (Teams card, email, ticket, web
    UI), then resumes the workflow by calling

        await workflow.run(responses={request_id: HumanDecision(...)})

    The `@response_handler` below is what the SDK calls when that response
    arrives.
    """

    def __init__(self, id: str = "human_approval") -> None:
        super().__init__(id=id)

    @handler
    async def review(
        self,
        message: AgentExecutorResponse,
        ctx: WorkflowContext[Never, FinalDecision],
    ) -> None:
        await ctx.request_info(
            request_data=ApprovalRequest(
                recommendation=message.agent_response.text or "",
                upstream_messages=list(message.full_conversation),
            ),
            response_type=HumanDecision,
        )

    @response_handler
    async def on_decision(
        self,
        request: ApprovalRequest,
        response: HumanDecision,
        ctx: WorkflowContext[Never, FinalDecision],
    ) -> None:
        final = FinalDecision(
            decision=response.decision,
            recommendation=response.recommendation or request.recommendation,
            human_comment=response.comment,
            upstream_messages=request.upstream_messages,
        )
        await ctx.yield_output(final)


# ---------------------------------------------------------------------------
# Notification channel (pluggable)
# ---------------------------------------------------------------------------

async def notify_human(request: ApprovalRequest, request_id: str) -> HumanDecision:
    """
    Deliver an approval request to a human and return their decision.

    The default implementation prints to the terminal and reads `input()`.
    Swap the body for any real channel — examples are stubbed below.
    """
    # --- 1. Teams Incoming Webhook (uncomment & set TEAMS_WEBHOOK_URL) -----
    # import httpx
    # webhook = os.environ.get("TEAMS_WEBHOOK_URL")
    # if webhook:
    #     async with httpx.AsyncClient() as http:
    #         await http.post(webhook, json={
    #             "text": f"Underwriting approval needed (request {request_id}):\n"
    #                     f"```\n{request.recommendation}\n```\n"
    #                     f"Reply via the approval portal.",
    #         })
    #     # then poll an approvals queue / DB row keyed by request_id ...

    # --- 2. Email (uncomment & wire SMTP / Graph) --------------------------
    # send_email(to="underwriters@contoso.com",
    #            subject=f"Approval needed [{request_id}]",
    #            body=request.recommendation)

    # --- 3. Default: terminal prompt --------------------------------------
    print("\n" + "=" * 78)
    print(f"HUMAN-IN-THE-LOOP  —  request {request_id}")
    print("=" * 78)
    print("Matcher's proposed recommendation:\n")
    print(request.recommendation)
    print("\nOptions:  [a]pprove   [e]dit   [r]eject")
    print("-" * 78)

    choice = (await asyncio.to_thread(input, "Your decision (a/e/r): ")).strip().lower()

    if choice.startswith("e"):
        print("Enter the edited JSON recommendation, finish with an empty line:")
        lines: list[str] = []
        while True:
            line = await asyncio.to_thread(input, "")
            if line == "":
                break
            lines.append(line)
        edited = "\n".join(lines) or request.recommendation
        comment = await asyncio.to_thread(input, "Comment for audit log: ")
        return HumanDecision(decision="edited", recommendation=edited, comment=comment)

    if choice.startswith("r"):
        comment = await asyncio.to_thread(input, "Reason for rejection: ")
        return HumanDecision(decision="rejected", comment=comment)

    return HumanDecision(decision="approved")


# ---------------------------------------------------------------------------
# Build & run workflow
# ---------------------------------------------------------------------------

async def main() -> int:
    classifier, extractor, matcher, mcp_tool = build_agents()

    # Wrap the chat agents as workflow nodes.
    classifier_node = AgentExecutor(classifier, id="classifier")
    extractor_node = AgentExecutor(extractor, id="extractor")
    matcher_node = AgentExecutor(matcher, id="matcher")
    approval_node = HumanApprovalExecutor()

    workflow = (
        WorkflowBuilder(start_executor=classifier_node)
        .add_chain([classifier_node, extractor_node, matcher_node, approval_node])
        .build()
    )

    print("=" * 78)
    print("Lab 9 — Multi-agent submission triage (real Agent Framework workflow)")
    print("=" * 78)
    print(f"APIM gateway : {APIM_URL}")
    print(f"MCP server   : {MCP_URL}")
    print(f"Deployment   : {DEPLOYMENT}")
    print()
    print("Inbound submission:")
    print("-" * 78)
    print(SUBMISSION_TEXT)
    print("-" * 78)
    print()

    initial_request = AgentExecutorRequest(
        messages=[Message(role="user", contents=[SUBMISSION_TEXT])],
        should_respond=True,
    )

    # `Workflow.run()` is awaitable when stream=False; it returns a
    # WorkflowRunResult (a list of WorkflowEvents).
    async with mcp_tool:
        result = await workflow.run(initial_request)

        # Suspend / resume loop. Each iteration: collect any pending
        # request_info events the workflow emitted, deliver them to a human
        # through `notify_human`, then resume with `responses=`.
        while True:
            pending = result.get_request_info_events()
            if not pending:
                break
            responses: dict[str, Any] = {}
            for ev in pending:
                request: ApprovalRequest = ev.data
                decision = await notify_human(request, ev.request_id or "")
                responses[ev.request_id] = decision
            result = await workflow.run(responses=responses)

    outputs = result.get_outputs()
    if not outputs:
        print("No workflow output produced.")
        return 1

    final: FinalDecision = outputs[-1]
    print("\n" + "=" * 78)
    print(f"FINAL DECISION: {final.decision.upper()}")
    print("=" * 78)
    print(final.recommendation)
    if final.human_comment:
        print(f"\nHuman comment: {final.human_comment}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
