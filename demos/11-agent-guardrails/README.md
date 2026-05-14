# Demo 11: Agent Guardrails

Demonstrates input/output safety guardrails using Azure AI Content Safety.

## Question Answered

> "We have concerns about prompt injection, off-topic requests, and PII leakage.
> We want content safety before and after the agent runs."

## What This Demonstrates

- **Input guardrails**: Filter harmful, off-topic, and PII-containing inputs
- **Output guardrails**: Prevent PII leakage and harmful content in responses
- **Azure AI Content Safety**: Severity scoring for text content
- **Custom blocklists**: Insurance-specific blocked terms
- **Agent-level policy**: Wrap agent invocation with pre/post checks

## Run

```bash
# Run guardrails demo
python demo_guardrails.py
```
