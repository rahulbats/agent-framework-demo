# Submission Agent Accelerator

End-to-end reference implementation combining all demos into a complete
insurance submission processing pipeline.

## What This Is

A production-ready accelerator that can be adapted for any submission workflow.
Combines:

- **Demo 01**: Agent with insurance tools
- **Demo 02**: Containerized for Foundry
- **Demo 05**: Full OTEL observability
- **Demo 09**: Multi-agent supervisor pattern
- **Demo 11**: Input/output guardrails

## Pipeline

```
Broker uploads → Document OCR → Classify → Extract → Match → Recommend
                      │              │          │         │         │
                  Doc Intelligence  LLM      LLM     Guidelines   LLM
                                                       DB
```

## Run

```bash
python submission_pipeline.py
```

## Adapting for Production

1. Replace mock tools with real API calls (policy management system, policy DB)
2. Update guideline matching rules from underwriting handbook
3. Connect to document storage (Azure Blob or SharePoint)
4. Deploy as containerized agent in Foundry Agent Service
5. Add Azure DevOps pipeline from Demo 12
