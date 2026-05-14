// Cost-threshold scheduled-query alert rule.
//
// Fires when an agent version's hourly token cost (very rough estimate using
// gen_ai.usage.total_tokens) crosses the threshold. The rule emits two
// dimensions — agent_name + agent_version — that the Logic App reads.

param location string = resourceGroup().location
param alertName string = 'agent-cost-threshold'

@description('App Insights component resource ID (the data source for the KQL).')
param appInsightsId string

@description('Action group resource ID to fire on alert.')
param actionGroupId string

@description('USD/hour threshold to trigger the kill.')
param thresholdUsd int = 10

resource alert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertName
  location: location
  properties: {
    displayName: alertName
    description: 'Kill any Foundry agent version exceeding hourly token cost.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [ appInsightsId ]
    targetResourceTypes: [ 'microsoft.insights/components' ]
    criteria: {
      allOf: [
        {
          query: '''
dependencies
| where timestamp > ago(15m)
| where customDimensions has "gen_ai.agent.name"
| extend agent_name = tostring(customDimensions["gen_ai.agent.name"])
| extend agent_version = tostring(coalesce(customDimensions["gen_ai.agent.version"], "1"))
| extend total_tokens = toint(customDimensions["gen_ai.usage.total_tokens"])
// Rough cost: gpt-4o ~ $5 / 1M input + $15 / 1M output. Use $10/1M blended.
| extend cost_usd = total_tokens * 10.0 / 1000000.0
| summarize cost_usd = sum(cost_usd) by agent_name, agent_version
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'cost_usd'
          dimensions: [
            { name: 'agent_name', operator: 'Include', values: [ '*' ] }
            { name: 'agent_version', operator: 'Include', values: [ '*' ] }
          ]
          operator: 'GreaterThan'
          threshold: thresholdUsd
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [ actionGroupId ]
      customProperties: {}
    }
  }
}
