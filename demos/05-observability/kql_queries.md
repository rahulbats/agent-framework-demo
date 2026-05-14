# KQL Queries — Demo 05 Observability

These queries target the App Insights workspace `agent-framework-appi` connected to
the Foundry project. All agent telemetry lands in the **`dependencies`** table.

Span types you'll see:

| `name`                                          | Source                | Notes                                              |
|-------------------------------------------------|-----------------------|----------------------------------------------------|
| `invoke_agent <agent-name>`                     | Foundry SDK           | One per agent invocation                           |
| `process_thread_run`                            | Foundry SDK           | Server-side run loop                               |
| `create_message` / `submit_tool_outputs`        | Foundry SDK           | Per turn / per tool batch                          |
| `tool.<function_name>`                          | This demo (manual)    | Local tool execution (e.g. `tool.read_document`)   |
| `demo.turn`                                     | This demo (manual)    | Wraps one user prompt → final response             |

Common `customDimensions` keys (parse with `customDimensions["..."]` or `tostring(...)`):

- `gen_ai.system` — always `"az.ai.agents"` for Foundry-emitted spans
- `gen_ai.operation.name` — `invoke_agent`, `create_message`, etc.
- `gen_ai.request.model`, `gen_ai.response.model`
- `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`
- `agent_id`, `thread_id`, `run_id`
- On `demo.turn` (custom): `agent.name`, `agent.version`, `variant`, `turn`, `prompt`, `response.length`

---

## 1. Recent agent activity (last 30 min)

```kusto
dependencies
| where timestamp > ago(30m)
| where name startswith "invoke_agent" or name == "demo.turn" or name startswith "tool."
| project timestamp, name, duration, success, operation_Id
| order by timestamp desc
```

## 2. Token usage and estimated cost per agent invocation

```kusto
let pricePerMTokInput  = 2.50;   // USD / 1M input tokens (gpt-4o)
let pricePerMTokOutput = 10.00;  // USD / 1M output tokens (gpt-4o)
dependencies
| where timestamp > ago(1h)
| where name startswith "invoke_agent"
| extend in_tok  = toint(customDimensions["gen_ai.usage.input_tokens"]),
         out_tok = toint(customDimensions["gen_ai.usage.output_tokens"]),
         model   = tostring(customDimensions["gen_ai.response.model"])
| extend cost_usd = (in_tok * pricePerMTokInput + out_tok * pricePerMTokOutput) / 1000000.0
| project timestamp, model, in_tok, out_tok, cost_usd, duration_ms = duration, operation_Id
| order by timestamp desc
```

## 3. Cost rollup per `demo.turn`

```kusto
let pricePerMTokInput  = 2.50;
let pricePerMTokOutput = 10.00;
let turns =
    dependencies
    | where timestamp > ago(1h)
    | where name == "demo.turn"
    | project turn_op = operation_Id,
              variant = tostring(customDimensions["variant"]),
              version = tostring(customDimensions["agent.version"]),
              prompt  = tostring(customDimensions["prompt"]),
              turn_duration_ms = duration;
let usage =
    dependencies
    | where timestamp > ago(1h)
    | where name startswith "invoke_agent"
    | summarize in_tok  = sum(toint(customDimensions["gen_ai.usage.input_tokens"])),
                out_tok = sum(toint(customDimensions["gen_ai.usage.output_tokens"]))
              by turn_op = operation_Id;
turns
| join kind=leftouter usage on turn_op
| extend cost_usd = (in_tok * pricePerMTokInput + out_tok * pricePerMTokOutput) / 1000000.0
| project variant, version, prompt, in_tok, out_tok, cost_usd, turn_duration_ms
| order by cost_usd desc
```

## 4. V1 vs V2 A/B comparison

```kusto
dependencies
| where timestamp > ago(1h)
| where name == "demo.turn"
| extend variant = tostring(customDimensions["variant"]),
         version = tostring(customDimensions["agent.version"]),
         resp_len = toint(customDimensions["response.length"])
| summarize turns           = count(),
            avg_latency_ms  = avg(duration),
            p95_latency_ms  = percentile(duration, 95),
            avg_response_chars = avg(resp_len)
          by variant, version
| order by variant asc
```

## 5. Tool call latency (P50 / P95) and failure rate

```kusto
dependencies
| where timestamp > ago(1h)
| where name startswith "tool."
| summarize calls = count(),
            failures = countif(success == false),
            p50_ms = percentile(duration, 50),
            p95_ms = percentile(duration, 95)
          by tool = name
| extend failure_rate = round(100.0 * failures / calls, 2)
| order by calls desc
```

## 6. End-to-end timeline for a single turn

Pick an `operation_Id` from query #1 and substitute below.

```kusto
let opId = "REPLACE_WITH_operation_Id";
dependencies
| where operation_Id == opId
| project timestamp, name, duration, success,
          model = tostring(customDimensions["gen_ai.response.model"]),
          in_tok  = toint(customDimensions["gen_ai.usage.input_tokens"]),
          out_tok = toint(customDimensions["gen_ai.usage.output_tokens"])
| order by timestamp asc
```

## 7. Top-N most expensive turns

```kusto
let pricePerMTokInput  = 2.50;
let pricePerMTokOutput = 10.00;
dependencies
| where timestamp > ago(24h)
| where name startswith "invoke_agent"
| extend cost_usd = (toint(customDimensions["gen_ai.usage.input_tokens"]) * pricePerMTokInput
                   + toint(customDimensions["gen_ai.usage.output_tokens"]) * pricePerMTokOutput) / 1000000.0
| top 10 by cost_usd desc
| project timestamp, model = tostring(customDimensions["gen_ai.response.model"]), cost_usd, duration_ms = duration, operation_Id
```

## 8. Errors and exceptions

```kusto
union dependencies, exceptions
| where timestamp > ago(1h)
| where success == false or itemType == "exception"
| project timestamp, itemType, name, outerMessage = tostring(coalesce(outerMessage, "")), operation_Id
| order by timestamp desc
```

## 9. Token throughput over time (5-min buckets)

```kusto
dependencies
| where timestamp > ago(6h)
| where name startswith "invoke_agent"
| extend in_tok  = toint(customDimensions["gen_ai.usage.input_tokens"]),
         out_tok = toint(customDimensions["gen_ai.usage.output_tokens"])
| summarize input_tokens  = sum(in_tok),
            output_tokens = sum(out_tok),
            invocations   = count()
          by bin(timestamp, 5m)
| order by timestamp asc
| render timechart
```

## 10. Tool call mix per variant

```kusto
let turns =
    dependencies
    | where timestamp > ago(1h)
    | where name == "demo.turn"
    | project turn_op = operation_Id,
              variant = tostring(customDimensions["variant"]);
let tools =
    dependencies
    | where timestamp > ago(1h)
    | where name startswith "tool."
    | project turn_op = operation_Id, tool = name;
turns
| join kind=inner tools on turn_op
| summarize tool_calls = count() by variant, tool
| order by variant asc, tool_calls desc
```
