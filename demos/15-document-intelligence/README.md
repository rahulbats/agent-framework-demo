# Lab 15 — Document Intelligence + Content Understanding

## Question Answered

Brokers don't email you clean JSON. They send PDFs of ACORD forms,
scanned loss runs, and prose-heavy cover emails. **How do you get from
"messy submission packet" to "structured payload the underwriting agent
can reason over" — without burning tokens on raw OCR or hallucinating
missing fields?**

## Approach

Two complementary services sit in front of the underwriting agent:

| Service                    | Best for                                                   | Output                      |
| -------------------------- | ---------------------------------------------------------- | --------------------------- |
| **Document Intelligence**  | Forms with known layouts (ACORDs, invoices, ID docs)       | Deterministic key-value JSON, layout, tables |
| **Content Understanding**  | Loose prose, multimodal (email/audio/video), schema-driven | Schema-typed JSON via LLM-reasoned extraction |

Rule of thumb:

- **Layout is fixed → DocIntel.** Cheap, deterministic, no hallucination risk.
- **Layout varies, you need reasoning ("did they hint at a claim they didn't disclose?") → Content Understanding.**

The intake pipeline calls both, merges the outputs, and hands a clean
normalized payload to the existing `insurance-submission-agent` from Lab 1.
The agent never sees raw OCR.

## What's in this lab

```
15-document-intelligence/
├── README.md
├── setup_cu_analyzer.py            # one-time: registers the broker-email analyzer
├── demo_doc_intel.py               # DocIntel only — application form
├── demo_content_understanding.py   # CU only — broker email
├── demo_intake_pipeline.py         # deterministic ETL: DI -> CU -> normalize -> underwrite
├── demo_intake_agent.py            # confidence-routed workflow + intake queue + HITL park
├── hitl_service.py                 # production scaffold: Teams notify + Entra-protected resume API
└── data/
    └── broker_email.txt            # synthetic broker cover email
```

The application form is reused from `demos/01-local-agent-dev/data/`.

## Resources used

| Resource                              | Created by                         |
| ------------------------------------- | ---------------------------------- |
| `agent-framework-docintel` (S0)       | Lab 15 setup (one-time)            |
| `broker-submission-email` CU analyzer | `setup_cu_analyzer.py`             |
| Foundry project + agent               | Reused from Labs 1 / 13            |
| Azure AI Search KB (optional)         | Lab 13 (`insurance-underwriting-kb`) |

## Run it

```pwsh
cd demos\15-document-intelligence

# 1. One-time: provision the Content Understanding analyzer
python setup_cu_analyzer.py

# 2. DocIntel-only demo
python demo_doc_intel.py

# 3. Content Understanding-only demo
python demo_content_understanding.py

# 4a. Deterministic pipeline (recommended for production ETL)
python demo_intake_pipeline.py
python demo_intake_pipeline.py --no-agent   # skip the underwriting handoff

# 4b. Confidence-routed workflow (DI -> CU -> HITL fallback)
python demo_intake_agent.py                              # broker_email -> CU branch
python demo_intake_agent.py --doc application_form       # form -> DI succeeds, no CU needed
python demo_intake_agent.py --force-hitl                 # demo HITL escalation

# 4c. Intake queue: serial DI/CU, but HITL parks the doc and the worker moves on
python demo_intake_agent.py --batch                      # all 3 fixtures, finishes inline if confident
python demo_intake_agent.py --batch --force-hitl         # all 3 park in phase 1, prompts in phase 2
```

## Intake queue behavior (`--batch`)

Processing is **serial by design** - one doc at a time hitting DocIntel and
Content Understanding so we don't blow up rate limits. The twist: HITL must
never block the queue.

```
phase 1 - drain queue:
  >> dequeue application_form.txt   -> DONE      (DI was enough)
  >> dequeue broker_email.txt       -> PARKED    (low conf, awaits human)
  >> dequeue loss_run_2024.txt      -> DONE

phase 2 - resolve parked:
  >> resume broker_email.txt (parked for 12.4s)
     [human prompt]            -> DONE
```

The `ParkedDoc` dataclass is the unit you'd persist (Cosmos / Service Bus +
dedup table) in production. The resume call is just `workflow.run(responses=...)`
with the `request_id` as the key.

## Production HITL: notification + Entra authorization (`hitl_service.py`)

The console prompt is fine for the lab, but in production you need two things
the demo can't do: **notify** the right reviewers and **authorize** their
resumes. `hitl_service.py` is a reference scaffold for both.

**Delivery options** (pick one - all key on the same `request_id`):

| Channel                       | How                                                          |
| ----------------------------- | ------------------------------------------------------------ |
| Teams Adaptive Card           | Incoming Webhook (in this scaffold) or Graph `chats/{id}/messages` for @mentions |
| Power Automate                | "Post adaptive card and wait for response" connector         |
| Outlook Actionable Message    | Embedded card with `Action.Http` to your resume endpoint     |
| ServiceNow / Jira             | Webhook -> ticket -> resolution callback                     |
| Custom reviewer portal        | List view of parked docs, claim + submit                     |

**Authorization** layers on top of standard Entra (use all three):

1. **Authentication**: reviewer client is an Entra app registration; backend validates the bearer token against `https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys` and pins `aud` and `iss`.
2. **App role membership**: define an `Underwriter` app role on the app registration, assign an Entra security group to it, enforce `"Underwriter" in claims["roles"]` on `/resume`.
3. **Per-request scoping**: stamp `assigned_to: [oid, ...]` and/or `required_role` on the parked doc when you park it (driven by NAICS / premium size / line of business). Resume handler enforces this on top of the role claim.

Management-side: use **Entra ID Governance Access Packages** for time-boxed,
approval-gated membership in the reviewer group, and a **Conditional Access**
policy on the resume endpoint that requires MFA + compliant device. Your code
just trusts the role claim - Entra does the rest.

**Audit** every resume to App Insights / Sentinel: `request_id`, `doc_path`,
reviewer `oid` + `upn`, parked duration, exact `field_overrides`. This is the
artifact a regulator will ask for.

Run the scaffold (won't be invoked by the demo, just for reference):

```pwsh
pip install fastapi uvicorn "pyjwt[crypto]" httpx
$env:AAD_TENANT_ID = "<tenant-guid>"
$env:AAD_AUDIENCE  = "api://<your-app-id>"
$env:REVIEWER_ROLE = "Underwriter"
uvicorn hitl_service:app --reload --port 8080
```

## Pipeline vs Workflow - which one when?

|                          | `demo_intake_pipeline.py`                  | `demo_intake_agent.py` (workflow)                 |
| ------------------------ | ------------------------------------------ | ------------------------------------------------- |
| Control flow             | Hard-coded: DI -> CU -> merge              | Confidence-routed: DI; if low conf -> CU; if low conf -> HITL |
| Cost on easy inputs      | Always pays for both DI + CU               | Pays only for what's needed (often just DI)       |
| Latency on easy inputs   | DI + CU sequential                         | DI only when it's enough                          |
| Failure recovery         | One bad doc breaks the run                 | Falls back to next stage / human                  |
| HITL                     | None - silent on missing data              | Real `ctx.request_info()` suspend/resume          |
| When to use              | Inputs are reliably structured             | Heterogeneous packets, SLAs around accuracy       |

**The workflow demonstrates three patterns at once**:
1. **Cheapest extractor first** - DocIntel is deterministic and fast. Try it before reaching for an LLM.
2. **LLM as fallback** - Content Understanding only runs when DI couldn't get to threshold. You don't pay LLM tokens unless you need to.
3. **Human as last resort** - if both extractors leave required fields empty, suspend the workflow with `ctx.request_info()` and resume after a human fills them in. Same pattern as Lab 9.

The confidence score is intentionally simple: `populated_required_fields / total_required_fields`. In production you'd combine this with DI's per-field confidence scores and CU's `score` metadata to get a more honest signal.

## Gotchas

- **DocIntel tier**: We provisioned **S0** (pay-per-page, ~$1.50/1000
  pages for prebuilt-layout). F0 is free but limited to 500 pages/month
  and one instance per subscription.
- **CU regional availability**: Content Understanding is in a subset of
  regions. If `setup_cu_analyzer.py` 404s, your Foundry account is in
  an unsupported region — create a separate AIServices account in
  `westus` / `swedencentral` and point `FOUNDRY_ENDPOINT` at it.
- **Plain `.txt` files** produce trivial DocIntel output (no layout to
  detect). For a real demo, point `demo_doc_intel.py` at a scanned
  PDF — the lab uses the `.txt` fixture from Lab 1 for portability.
- **CU schema changes** require deleting + recreating the analyzer
  (`python setup_cu_analyzer.py --delete` then re-run). Versioning
  analyzers via name suffix (`broker-submission-email-v2`) is the
  production pattern.
- **MI auth on DocIntel**: `DefaultAzureCredential` works because we
  used `--custom-domain` at create time. AAD on Cognitive Services
  requires a custom-domain endpoint.

## Where this fits

```
broker → email + PDF → [Lab 15: intake]  → normalized JSON
                                         ↓
              [Lab 1/2: submission agent] → VERDICT
                                         ↓
              [Lab 13: KB lookups for guidelines]
                                         ↓
              [Lab 10: continuous eval on the decision]
                                         ↓
              [Lab 12: ship new agent versions safely]
```
