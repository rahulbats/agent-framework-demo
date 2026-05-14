# Demo 10: Agent Evaluation

Quality-gating an agent before promoting it to production.

## Question Answered

> "This will be the quality gate to decide if we can release an agent to production.
> We will have a particular metric, and if it exceeds a threshold, the agent is ready."
>
> "For this agent, for this metric, this is the trend. Version 1: 32, Version 2: 35."

## Approach

Evaluate the existing **`insurance-submission-agent`** that's already in your
project `rahul-agent-framework-project` from the Azure AI Foundry portal.
Results appear under the **Evaluation** tab with version-over-version trends,
side-by-side comparison, and continuous-eval hooks for the production gate.

---

## Evaluate the hosted agent in the Foundry portal

### 1. Build a synthetic eval dataset (one-time)

Foundry needs a JSONL dataset with at least a `query` column (and optionally
`ground_truth` / `context`). Two ways to make one:

**a) Generate it from the portal**
1. Open Azure AI Foundry → your project → **Data + indexes** → **+ New dataset**.
2. Pick **Generate with AI** → choose source = your insurance docs
   (upload the files in [`demos/01-local-agent-dev/data/`](../01-local-agent-dev/data/)
   or point at an existing index).
3. Set **# of questions** = 20, **Task** = "Underwriting Q&A".
4. Save as `insurance-eval-v1` (JSONL).

**b) Or upload one you wrote**

Save the following as `insurance-eval.jsonl` and upload via **Data + indexes
→ + New dataset → Upload local file**:

```jsonl
{"query": "What is the verdict for the Acme Manufacturing submission?", "ground_truth": "REFER"}
{"query": "List the top 3 red flags in loss_run_2024.txt.", "ground_truth": "high frequency, cyber incident, property losses"}
{"query": "Does the submission qualify for the CP-100 program?", "ground_truth": "Yes for Georgia, no for Alaska"}
{"query": "What coverage limit is requested for Cyber Liability?", "ground_truth": "$1M"}
{"query": "Summarize the prior policy renewal terms.", "ground_truth": "12-month term, $5M building, $2M contents"}
```

### 2. Create the evaluation run

1. Foundry portal → your project → **Evaluation** → **+ Create new evaluation**.
2. **Evaluate what?** → **Agent**.
3. **Agent** → pick `insurance-submission-agent`. Pin a specific **version**
   (e.g. `1`) so the run is reproducible. Repeat the whole flow later with
   version `14` for the V1 vs V14 comparison.
4. **Dataset** → pick the dataset you created in step 1.
5. **Column mapping** → map `query` → user input, `ground_truth` → reference.
6. **Evaluators** — tick the ones you actually want to gate on:
   - **Groundedness** — answer is supported by retrieved context
   - **Relevance** — answer addresses the question
   - **Similarity** — semantic match against `ground_truth`
   - **Coherence** / **Fluency** — quality scorers
   - **Content safety** (Hate, Violence, Sexual, Self-harm) — only if the agent
     touches user-generated text
   - Optionally a **custom prompt evaluator**: "Did the response include a
     one-line VERDICT (ACCEPT / DECLINE / REFER)?" — matches the agent's
     instruction prompt
7. **Connection** for the AI-assisted scorers → your `gpt-4o` deployment
   (the same one APIM is fronting in Lab 7).
8. **Name** → `insurance-submission-agent v1 — May 13 2026`. Submit.

### 3. Read the results in the portal

When the run finishes (a few minutes for ~20 rows):

- **Evaluations** list → click the run → **Metrics** tab shows aggregate scores
  per evaluator.
- **Data** tab shows per-row inputs / outputs / evaluator scores → click any
  row to drill into the trace.
- Re-run the same flow against version `14` of the agent. Foundry's
  **Compare** button puts the two runs side by side with a delta column —
  that's the "V1: 32, V2: 35" trend the question asked for.

### 4. Wire it into the quality gate

Once you trust the evaluators, automate the gate:

- **Continuous evaluation**: Foundry portal → **Evaluation** → **Continuous
  evaluation** → attach the same evaluator set to live agent traces. Each
  production call gets scored automatically; you set thresholds and alerts.
- **Scheduled evaluation**: Same screen → **Schedule** → run the dataset on a
  cadence (e.g. nightly) and trend the metric over time.
- **CI gate**: from `azure-pipelines.yml`, call
  `az ml job create` (or the REST endpoint of the schedule) and read the
  resulting metrics with `az ml job show`. Fail the pipeline if the score
  drops below threshold.

---

## Files

- This README — portal walkthrough
