# Demo 14: Batch Inference

Process **thousands of insurance submissions overnight** through the agent's
underlying model using the **Azure OpenAI Batch API** — 24h SLA, 50 %
discount vs. real-time, no rate-limit pressure on the production deployment.

## Question Answered

> "Brokers send us hundreds of submissions a day. We don't need each answer
> in 2 seconds — we need a triage verdict on every one by next morning. Can
> we get a discount and stop competing with our online traffic for TPM?"

## What this demonstrates

- Build a **JSONL batch file**: one chat-completion request per submission,
  pre-prompted with the same instructions the hosted agent uses.
- Upload via the **Files API** (`purpose="batch"`).
- Submit a **batch job** with a **24h completion window**.
- Poll status, download the output JSONL, and parse one verdict per row.
- Wire results into a downstream pipeline (CSV for underwriters, eventually
  the same persistence layer the real-time agent uses).

## When to use Batch vs. Real-time vs. Hosted Agent

| Need | Use |
|---|---|
| < 5s p95, conversational, tool calls, HITL | Hosted agent (Lab 9) |
| Fire-and-forget, < 24h, large volume, deterministic prompt | **Batch API (this lab)** |
| One-off, interactive in a notebook | Direct chat-completion call |

Batch trades latency for throughput + price. Per-token cost is 50 % of
synchronous, the per-deployment TPM/RPM limits don't apply, and you submit
up to 50 000 requests in a single file.

## Prereqs

- Azure OpenAI deployment of `gpt-4o` in your Foundry resource (already in
  place from earlier labs). Batch is supported on `gpt-4o`, `gpt-4o-mini`,
  `gpt-4.1`, and `o3-mini` SKUs.
- `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` in `.env` (or use
  `DefaultAzureCredential` — the demo supports both).
- Python deps: `openai`, `python-dotenv` (already in repo
  `requirements.txt`).

## Run it

```powershell
.\.venv\Scripts\python.exe demos\14-batch-inference\demo_batch_inference.py
```

What it does, end-to-end:

1. Reads the 8 sample submissions in
   [submissions.jsonl](submissions.jsonl).
2. Generates `batch_input.jsonl` — one
   `/chat/completions` request per submission, all using the underwriter
   system prompt.
3. Uploads the file (`files.create(purpose="batch")`).
4. Submits the batch (`batches.create(completion_window="24h")`).
5. Polls every 30s until status is `completed`, `failed`, or `expired`.
6. Downloads the output file and prints a verdict table:

   ```
   submission_id   insured                verdict     reasoning (1 line)
   sub-001         Acme Logistics         APPROVE     Loss ratio within …
   sub-002         Northwind Shipping     DECLINE     3 at-fault losses …
   sub-003         Contoso Couriers       REFER       Coverage requested …
   …
   ```

7. Writes `batch_output.jsonl` (raw) and `batch_results.csv`
   (underwriter-friendly).

For the demo, the script also accepts `--window 1h` to use the **express
1-hour window** when you don't want to wait overnight (no discount, still
isolated from real-time TPM).

## Cost & sizing notes

- Batch is billed at **50 %** of synchronous per-token rates.
- A single batch file may contain up to **50 000 requests** and **200 MB**.
- Token throughput per workspace is gated by the **Batch enqueued tokens**
  quota, separate from the deployment's real-time TPM. Check it under
  *Foundry → Quotas → Batch*.
- Failed rows show up in `error_file_id` — the script downloads and prints
  them too so you can see schema / content-filter failures without
  re-submitting the whole file.

## Where this fits in the broader pipeline

```
   brokers ──► intake queue ──┬──► real-time agent (Labs 1–9)   ◄ urgent / interactive
                              │
                              └──► nightly batch (this lab)     ◄ everything else
                                            │
                                            ▼
                                  CSV → underwriter inbox
                                  + writeback to submission DB
```

The system prompt and tool-output schema are the **same** as the hosted
agent's, so the verdicts are directly comparable. That makes batch a
natural place to run periodic **quality regression** sweeps too — point
Lab 10's eval at `batch_output.jsonl` and you get a daily evaluator score
for free.

## Files

- [demo_batch_inference.py](demo_batch_inference.py) — end-to-end runner
- [submissions.jsonl](submissions.jsonl) — 8 sample submissions
- README.md — this file

## Gotchas

- The **first** call to `files.create(purpose="batch")` against a new
  resource can take 10–30s as the back-end provisions the batch storage
  account — this is normal, not a hang.
- Use `api_version="2025-04-01-preview"` or later — earlier versions don't
  expose `/batches`.
- Output rows come back in **arbitrary order** but every row carries the
  `custom_id` you sent in. Always join on `custom_id`, never on row index.
- Batch jobs are **per-deployment**: a job submitted against a Standard
  deployment can't move to a Provisioned one mid-flight.
