"""Demo 14 — Batch inference for the insurance underwriter agent.

Submits the system prompt + each submission as one row of a JSONL batch
file to the Azure OpenAI Batch API, polls until complete, and prints
verdicts. 50% cheaper than synchronous and isolated from real-time TPM.

Usage:
    python demo_batch_inference.py                # 24h window (cheapest)
    python demo_batch_inference.py --window 1h    # express, no discount
"""

import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parents[1] / "demos" / "01-local-agent-dev" / ".env")

ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
API_KEY = os.environ["AZURE_OPENAI_API_KEY"]
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
API_VERSION = "2025-04-01-preview"

SYSTEM_PROMPT = (
    "You are a senior commercial-insurance underwriter assistant. For each "
    "submission, return STRICT JSON with keys: verdict (APPROVE|DECLINE|REFER), "
    "reasoning (one sentence, <=160 chars), conditions (list of strings, may "
    "be empty). Apply standard underwriting guidelines: loss ratio <= 60%, no "
    "more than 2 at-fault losses in 24 months, no coverage in sanctioned "
    "regions. Return ONLY the JSON object, no prose."
)


def build_batch_input(submissions_path: Path, out_path: Path) -> int:
    """Write one /chat/completions request per submission. Returns row count."""
    n = 0
    with submissions_path.open() as src, out_path.open("w") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            sub = json.loads(line)
            request = {
                "custom_id": sub["id"],
                "method": "POST",
                "url": "/chat/completions",
                "body": {
                    "model": DEPLOYMENT,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(sub, indent=2)},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 400,
                    "temperature": 0.1,
                },
            }
            dst.write(json.dumps(request) + "\n")
            n += 1
    return n


def submit_and_wait(client: AzureOpenAI, input_path: Path, window: str) -> dict:
    print(f"[1/4] Uploading {input_path.name}…")
    uploaded = client.files.create(file=input_path.open("rb"), purpose="batch")
    print(f"      file_id = {uploaded.id}")

    print(f"[2/4] Submitting batch (window={window})…")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/chat/completions",
        completion_window=window,
        metadata={"app": "insurance-underwriter", "lab": "14"},
    )
    print(f"      batch_id = {batch.id}")

    print("[3/4] Polling status…")
    terminal = {"completed", "failed", "expired", "cancelled"}
    while batch.status not in terminal:
        time.sleep(30)
        batch = client.batches.retrieve(batch.id)
        counts = batch.request_counts
        print(
            f"      status={batch.status:<12} "
            f"completed={counts.completed}/{counts.total} "
            f"failed={counts.failed}"
        )

    if batch.status != "completed":
        raise RuntimeError(f"Batch ended in status={batch.status}: {batch.errors}")
    return batch


def download_results(client: AzureOpenAI, batch, raw_path: Path, csv_path: Path) -> None:
    print("[4/4] Downloading output…")
    out = client.files.content(batch.output_file_id)
    raw_path.write_bytes(out.read())

    rows = []
    for line in raw_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        custom_id = row.get("custom_id", "?")
        body = (row.get("response") or {}).get("body") or {}
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or "{}"
        try:
            verdict = json.loads(content)
        except json.JSONDecodeError:
            verdict = {"verdict": "PARSE_ERROR", "reasoning": content[:160], "conditions": []}
        rows.append({"submission_id": custom_id, **verdict})

    rows.sort(key=lambda r: r["submission_id"])
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["submission_id", "verdict", "reasoning", "conditions"])
        w.writeheader()
        for r in rows:
            r["conditions"] = "; ".join(r.get("conditions") or [])
            w.writerow(r)

    print("\n" + "=" * 70)
    print(f"{'submission_id':<14} {'verdict':<10} reasoning")
    print("=" * 70)
    for r in rows:
        print(f"{r['submission_id']:<14} {r['verdict']:<10} {r['reasoning'][:70]}")

    if batch.error_file_id:
        err_path = raw_path.with_name("batch_errors.jsonl")
        err_path.write_bytes(client.files.content(batch.error_file_id).read())
        print(f"\nNOTE: {err_path.name} contains row-level errors.")

    print(f"\nWrote {raw_path.name} (raw) and {csv_path.name} (table).")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", choices=["1h", "24h"], default="24h")
    parser.add_argument("--input", type=Path, default=HERE / "submissions.jsonl")
    args = parser.parse_args()

    client = AzureOpenAI(
        azure_endpoint=ENDPOINT,
        api_key=API_KEY,
        api_version=API_VERSION,
    )

    batch_input = HERE / "batch_input.jsonl"
    n = build_batch_input(args.input, batch_input)
    print(f"Built batch with {n} requests from {args.input.name}\n")

    batch = submit_and_wait(client, batch_input, args.window)
    download_results(
        client,
        batch,
        raw_path=HERE / "batch_output.jsonl",
        csv_path=HERE / "batch_results.csv",
    )


if __name__ == "__main__":
    main()
