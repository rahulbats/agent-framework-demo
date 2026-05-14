"""
Demo 07: LLM Gateway with APIM

Same OpenAI SDK your agent code already uses. Only change: point `azure_endpoint`
at the APIM gateway and pass an APIM subscription key instead of an AOAI key.
APIM applies the AI policies (managed-identity auth to AOAI, llm-token-limit,
llm-emit-token-metric, llm-semantic-cache-*) and forwards to gpt-4o.

`with_raw_response` lets us inspect the response headers the policies stamp on
(`x-tokens-consumed`, `x-ratelimit-remaining-tokens`, `retry-after`).

Required env (in demos/01-local-agent-dev/.env):
    APIM_GATEWAY_URL          e.g. https://rahul-ai-gateway.azure-api.net
    APIM_SUBSCRIPTION_KEY     APIM subscription key for the 'ai-gateway' product
    AOAI_DEPLOYMENT           e.g. gpt-4o
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import APIStatusError, AzureOpenAI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(Path(__file__).resolve().parents[1] / "01-local-agent-dev" / ".env")
console = Console()

GATEWAY = os.environ["APIM_GATEWAY_URL"].rstrip("/")
APIM_KEY = os.environ["APIM_SUBSCRIPTION_KEY"]
DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o")
API_VERSION = "2024-12-01-preview"

# Standard Azure OpenAI SDK client. The only thing different from talking to
# AOAI directly is that azure_endpoint points at APIM and the api_key is the
# APIM subscription key.
client = AzureOpenAI(
    azure_endpoint=GATEWAY,
    api_version=API_VERSION,
    # AzureOpenAI requires api_key, but APIM reads Ocp-Apim-Subscription-Key,
    # not api-key. We send the real key in default_headers and a placeholder here.
    api_key="unused",
    # SDK auto-retries on 429 by default; we want to see the gateway's response
    # ourselves so the demo can show retry-after.
    max_retries=0,
    default_headers={
        "Ocp-Apim-Subscription-Key": APIM_KEY,
        # APIM's llm-emit-token-metric policy reads these and tags the metric.
        "x-agent-name": "submission-agent",
        "x-session-id": "demo-session-001",
    },
)


def chat(prompt: str, *, max_tokens: int = 60, session: str | None = None):
    """One call. Returns (raw_response_with_headers, parsed_completion)."""
    headers = {"x-session-id": session} if session else None
    raw = client.chat.completions.with_raw_response.create(
        model=DEPLOYMENT,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        extra_headers=headers,
    )
    return raw, raw.parse()


def scenario_a_governed_call() -> None:
    """A: AzureOpenAI SDK -> APIM -> AOAI. Headers prove the policies fired."""
    console.print(
        Panel.fit(
            "[bold]Scenario A[/bold]: same OpenAI SDK your agent code uses.\n"
            "Only `azure_endpoint` and `api_key` changed. APIM signs the AOAI\n"
            "call with its managed identity, then attaches token-limit headers\n"
            "to the response.",
            title="A. SDK -> APIM -> AOAI",
            border_style="cyan",
        )
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("call")
    table.add_column("status")
    table.add_column("latency")
    table.add_column("x-tokens-consumed")
    table.add_column("x-ratelimit-remaining")

    for i in range(1, 4):
        t0 = time.perf_counter()
        raw, _ = chat(f"Give me one fun fact about underwriting (#{i}).")
        elapsed = int((time.perf_counter() - t0) * 1000)
        table.add_row(
            f"call {i}",
            str(raw.http_response.status_code),
            f"{elapsed} ms",
            raw.http_response.headers.get("x-tokens-consumed", "-"),
            raw.http_response.headers.get("x-ratelimit-remaining-tokens", "-"),
        )
    console.print(table)


def scenario_b_no_key_blocked() -> None:
    """B: wrong APIM key -> 401 from the gateway. AOAI is never called."""
    console.print(
        Panel.fit(
            "[bold]Scenario B[/bold]: a client with a bad APIM subscription key\n"
            "is rejected at the gateway. AOAI never sees the request.",
            title="B. Auth at the gateway",
            border_style="cyan",
        )
    )
    bad_client = AzureOpenAI(
        azure_endpoint=GATEWAY,
        api_version=API_VERSION,
        api_key="unused",
        default_headers={"Ocp-Apim-Subscription-Key": "not-a-real-key"},
    )
    try:
        bad_client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
    except APIStatusError as e:
        console.print(f"  status: [bold red]{e.status_code}[/bold red]")
        console.print(f"  body:   {str(e.message)[:200]}")


def scenario_c_token_limit() -> None:
    """C: hammer the gateway until llm-token-limit returns 429."""
    console.print(
        Panel.fit(
            "[bold]Scenario C[/bold]: drain the per-key TPM bucket. APIM short-\n"
            "circuits with 429 + retry-after; AOAI is not called for that turn.",
            title="C. Token-limit kicks in",
            border_style="cyan",
        )
    )
    big_prompt = "Write a 400-word essay about marine cargo insurance. " * 4
    for i in range(1, 16):
        t0 = time.perf_counter()
        try:
            raw, _ = chat(big_prompt, max_tokens=400, session=f"loop-{i}")
            elapsed = int((time.perf_counter() - t0) * 1000)
            consumed = raw.http_response.headers.get("x-tokens-consumed", "-")
            remaining = raw.http_response.headers.get("x-ratelimit-remaining-tokens", "-")
            console.print(
                f"  call {i:>2}  status=200  {elapsed:>5} ms  "
                f"consumed={consumed:>4}  remaining={remaining:>4}"
            )
        except APIStatusError as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            retry = e.response.headers.get("retry-after", "?") if e.response else "?"
            console.print(
                f"  call {i:>2}  status=[bold red]{e.status_code}[/bold red]  "
                f"{elapsed:>5} ms  retry-after={retry}s"
            )
            if e.status_code == 429:
                console.print("[bold yellow]  -> token-limit policy refused the call. Backend was not hit.[/bold yellow]")
                break
    else:
        console.print("[dim]  (did not hit the limit in 15 calls; raise prompt size or lower tpmPerKey)[/dim]")


def scenario_d_metrics_pointer() -> None:
    """D: explain how to see the per-agent metrics in App Insights."""
    console.print(
        Panel.fit(
            "[bold]Scenario D[/bold]: the [italic]llm-emit-token-metric[/italic] policy emits\n"
            "TokenMetric to App Insights with dimensions Agent + Session + Subscription.\n\n"
            "Query (Logs blade):\n"
            "  customMetrics\n"
            "  | where name == 'TokenMetric'\n"
            "  | summarize total=sum(value) by tostring(customDimensions.Agent),\n"
            "      tostring(customDimensions.Session), bin(timestamp, 5m)\n"
            "  | order by timestamp desc",
            title="D. Per-agent token attribution",
            border_style="cyan",
        )
    )


def main() -> None:
    console.rule("[bold]Demo 07: APIM AI Gateway[/bold]")
    console.print(f"Gateway: [cyan]{GATEWAY}[/cyan]   Deployment: [cyan]{DEPLOYMENT}[/cyan]\n")
    scenario_a_governed_call()
    scenario_b_no_key_blocked()
    scenario_c_token_limit()
    scenario_d_metrics_pointer()
    console.rule("[bold green]Done[/bold green]")


if __name__ == "__main__":
    main()
