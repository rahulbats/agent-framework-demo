// Logic App (Consumption) that receives an Azure Monitor common-alert-schema
// payload and kills the offending Foundry agent version.
//
// Wiring:
//   App Insights KQL alert  --> Action Group (LogicApp receiver)
//                           --> THIS Logic App
//                           --> DELETE {foundryEndpoint}/agents/{name}/versions/{ver}
//
// Auth: Logic App's system-assigned managed identity acquires a token for
//       audience https://ai.azure.com and calls Foundry directly.

param location string = resourceGroup().location
param logicAppName string = 'kill-switch-logicapp'

@description('Foundry account hostname, e.g. rahul-agent-framework-demo.services.ai.azure.com')
param foundryHost string

@description('Foundry project segment, e.g. /api/projects/rahul-agent-framework-project')
param foundryProjectPath string

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        foundryHost: { type: 'String', defaultValue: foundryHost }
        foundryProjectPath: { type: 'String', defaultValue: foundryProjectPath }
      }
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
            }
          }
        }
      }
      actions: {
        Init_Agent_Name: {
          runAfter: {}
          type: 'InitializeVariable'
          inputs: {
            variables: [
              { name: 'agentName', type: 'String', value: '' }
            ]
          }
        }
        Init_Agent_Version: {
          runAfter: { Init_Agent_Name: ['Succeeded'] }
          type: 'InitializeVariable'
          inputs: {
            variables: [
              { name: 'agentVersion', type: 'String', value: '' }
            ]
          }
        }
        Check_Fired: {
          runAfter: { Init_Agent_Version: ['Succeeded'] }
          type: 'If'
          expression: {
            and: [
              {
                equals: [
                  '@toLower(coalesce(triggerBody()?[\'data\']?[\'essentials\']?[\'monitorCondition\'], \'\'))'
                  'fired'
                ]
              }
            ]
          }
          actions: {
            For_Each_Dimension: {
              type: 'Foreach'
              foreach: '@coalesce(triggerBody()?[\'data\']?[\'alertContext\']?[\'condition\']?[\'allOf\']?[0]?[\'dimensions\'], json(\'[]\'))'
              actions: {
                If_Name: {
                  type: 'If'
                  expression: {
                    and: [
                      { equals: [ '@items(\'For_Each_Dimension\')?[\'name\']', 'agent_name' ] }
                    ]
                  }
                  actions: {
                    Set_Name: {
                      type: 'SetVariable'
                      inputs: {
                        name: 'agentName'
                        value: '@{items(\'For_Each_Dimension\')?[\'value\']}'
                      }
                    }
                  }
                }
                If_Version: {
                  runAfter: { If_Name: ['Succeeded', 'Skipped'] }
                  type: 'If'
                  expression: {
                    and: [
                      { equals: [ '@items(\'For_Each_Dimension\')?[\'name\']', 'agent_version' ] }
                    ]
                  }
                  actions: {
                    Set_Version: {
                      type: 'SetVariable'
                      inputs: {
                        name: 'agentVersion'
                        value: '@{items(\'For_Each_Dimension\')?[\'value\']}'
                      }
                    }
                  }
                }
              }
            }
            Delete_Agent_Version: {
              runAfter: { For_Each_Dimension: ['Succeeded'] }
              type: 'Http'
              inputs: {
                method: 'DELETE'
                uri: '@{concat(\'https://\', parameters(\'foundryHost\'), parameters(\'foundryProjectPath\'), \'/agents/\', variables(\'agentName\'), \'/versions/\', variables(\'agentVersion\'), \'?api-version=v1\')}'
                authentication: {
                  type: 'ManagedServiceIdentity'
                  audience: 'https://ai.azure.com'
                }
              }
            }
            Respond_Killed: {
              runAfter: { Delete_Agent_Version: ['Succeeded', 'Failed'] }
              type: 'Response'
              kind: 'Http'
              inputs: {
                statusCode: 200
                body: {
                  action: 'killed'
                  agent_name: '@variables(\'agentName\')'
                  agent_version: '@variables(\'agentVersion\')'
                  foundry_status: '@outputs(\'Delete_Agent_Version\')[\'statusCode\']'
                }
              }
            }
          }
          else: {
            actions: {
              Respond_Ignored: {
                type: 'Response'
                kind: 'Http'
                inputs: {
                  statusCode: 200
                  body: {
                    action: 'ignored'
                    reason: 'monitorCondition was not Fired'
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

output logicAppName string = logicApp.name
output principalId string = logicApp.identity.principalId
output logicAppId string = logicApp.id
