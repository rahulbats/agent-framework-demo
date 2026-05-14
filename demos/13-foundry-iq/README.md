# Demo 13: Foundry IQ — Federated Knowledge for Agents

Wire the **insurance-submission-agent** to **Microsoft Foundry IQ**: one
knowledge base over SharePoint + Azure AI Search + the loss-run/guidelines
files in `demos/01-local-agent-dev/data/`, queried through a single
retrieval endpoint instead of bespoke RAG plumbing per source.

## Question Answered

> "Underwriters' source material lives in SharePoint, Azure Search, and a
> bunch of policy PDFs in Blob. We don't want to build and maintain a
> retrieval pipeline per source — and we want the *same* knowledge to ground
> our agent, our chat app, and our search experience."

## What Foundry IQ is (in 30 seconds)

| Concept | What it is |
|---|---|
| **Knowledge Source** | A connector to one corpus — SharePoint site, OneLake table, Azure AI Search index, public web domain, or files in Blob/OneDrive. Foundry handles ingest, chunking, embedding, refresh. |
| **Knowledge Base** | A named bundle of one or more Knowledge Sources with shared ACLs and a single retrieval endpoint. |
| **Retrieval API** | `POST /knowledgeBases/{name}/retrieve` — fans out the query across all sources in the KB, merges, reranks, returns passages + citations. |
| **`grounding_with_knowledge_base` tool** | A built-in Foundry agent tool that calls the retrieval API on every turn — no custom tool code. |

This replaces three things you'd otherwise hand-build: per-source connectors,
a federated retriever, and the boilerplate that injects passages into the
prompt with citations.

## What this demo shows

1. Provision a Knowledge Base in the portal over the insurance corpus.
2. Attach it to `insurance-submission-agent` as a grounding tool (no code
   changes to the agent).
3. From Python, hit the retrieval API directly — useful for non-agent
   surfaces (search box, batch enrichment, eval pipelines).
4. Compare an agent answer with vs. without the KB attached so the
   citation/grounding lift is visible in traces and Lab 10's eval run.

---

## Step 1 — Create the Knowledge Sources (portal)

In **Azure AI Foundry → your project → Knowledge → Sources → + New source**,
create three sources for the insurance corpus:

| Source name | Type | Points at |
|---|---|---|
| `underwriting-guidelines` | **Files (Blob)** | Upload `demos/01-local-agent-dev/data/guidelines.json` and `coverage_summary.txt` |
| `loss-history` | **Files (Blob)** | Upload `loss_run_2024.txt` |
| `submission-forms` | **SharePoint** *(or Files if you don't have one)* | Your underwriting SharePoint site, or upload `application_form.txt` |

For each source, accept the defaults:
- Chunking: **Layout-aware**, ~800 tokens with 100 overlap
- Embedding: **text-embedding-3-large** (project default)
- Refresh: **Daily** for live sources, **On-demand** for static files

Each source gets its own ingestion job — wait for **Status = Ready** before
moving on (small corpora finish in 1–2 minutes).

## Step 2 — Bundle them into a Knowledge Base

**Knowledge → Knowledge bases → + New knowledge base**:

- **Name**: `insurance-underwriting-kb`
- **Sources**: tick all three from Step 1
- **Ranker**: **Semantic ranker** (default; required for citation quality)
- **Access**: leave at project scope for the demo; in production restrict
  via **Knowledge Base Reader** on a security group

After creation you get:

- **Retrieval endpoint**: `{FOUNDRY_PROJECT_ENDPOINT}/knowledgeBases/insurance-underwriting-kb/retrieve`
- A **Test pane** in the portal — paste a query and see ranked passages
  with source citations before wiring anything to the agent.

## Step 3 — Attach the KB to the existing agent

**Agents → `insurance-submission-agent` → + Add tool → Knowledge base**.
Pick `insurance-underwriting-kb`. Save → this creates a new agent **version**
(e.g. v15).

In the agent's instructions, add one line so it actually uses the tool:

> *"For any factual claim about coverage, exclusions, or prior losses,
> retrieve from the attached knowledge base and cite the source."*

That's the entire wiring. The agent's existing custom tools
(`list_submission_documents`, `read_document`, `search_underwriting_guidelines`)
stay; the KB tool sits alongside them and the model picks per turn.

## Step 4 — Run the demo

```powershell
.\.venv\Scripts\python.exe demos\13-foundry-iq\demo_foundry_iq.py
```

What it does:

1. Calls the retrieval endpoint **directly** with three underwriter questions
   (no agent in the loop) and prints the top passages + citations.
2. Invokes `insurance-submission-agent` v14 (no KB) and v15 (KB attached) on
   the same submission and prints both answers side by side so the
   grounding lift and citations are visible.

Expected output: v14 paraphrases the guidelines from memory; v15 quotes the
exact section and links back to the source document in the KB.

## Step 5 — Use it from non-agent surfaces

The same retrieval endpoint is what you point a search box, a Power Automate
flow, or a batch enrichment job at. The Python snippet in
[demo_foundry_iq.py](demo_foundry_iq.py) (`retrieve()` function) is the
entire client — no SDK ceremony, no per-source code.

## Step 6 — Re-run Lab 10 evaluation against the KB-grounded version

Foundry IQ's value is most visible on the **Groundedness** evaluator. In
the Foundry portal Evaluations tab (Lab 10):

1. Re-run the same eval dataset against `insurance-submission-agent` v15.
2. Use **Compare** to overlay v14 vs v15.
3. Groundedness and Similarity should jump materially; Relevance/Coherence
   should be flat or slightly up.

That's the trend chart that justifies the change in your release notes.

## Files

- [demo_foundry_iq.py](demo_foundry_iq.py) — direct retrieval-API client +
  v14-vs-v15 agent comparison
- README.md — this file

## Notes & gotchas

- The retrieval API is **bearer-auth with AAD** — `DefaultAzureCredential`
  works; no separate API key.
- ACLs on the underlying SharePoint/OneLake source are **honoured at query
  time** with on-behalf-of when the caller provides a user token. With a
  managed-identity caller (CI, batch jobs) you get the identity's
  permissions only — set source-level RBAC accordingly.
- KB refresh is incremental and async. After uploading new guideline files,
  hit **Refresh now** on the source or wait for the daily job; the KB
  endpoint reflects the change as soon as the source job completes.
- Cost: Foundry IQ bills on **ingested tokens** (one-time per chunk) +
  **retrieval calls**. The semantic ranker is included in the per-call
  price; embeddings reuse your project's embedding deployment quota.
