// Lab 8 — API Center as the MCP registry.
// Provisions an Azure API Center with one workspace, one environment, one API
// of kind 'mcp', one version, and one deployment that points at the
// streamable-http endpoint of mcp_server.py.
// Demo client (demo_mcp_registry.py) discovers the MCP URL from this catalog
// and runs the same agent loop without any hard-coded URL.

@description('API Center service name (globally unique).')
param apiCenterName string = 'rahul-api-center'

@description('Region. API Center is GA in eastus, westeurope, uksouth, centralindia, australiaeast, francecentral, swedencentral, canadacentral.')
param location string = 'eastus'

@description('Public URL for the running MCP server. Use a tunnelled URL (devtunnel/ngrok) to register a remotely reachable endpoint, otherwise localhost is fine for a local demo.')
param mcpRuntimeUri string = 'http://localhost:8081/mcp'

resource service 'Microsoft.ApiCenter/services@2024-06-01-preview' = {
  name: apiCenterName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  #disable-next-line BCP037
  sku: {
    name: 'Free'
  }
  properties: {}
}

// 'default' workspace is auto-created with the service, but declaring it
// makes the parent/child chain explicit and lets us nest API resources.
resource workspace 'Microsoft.ApiCenter/services/workspaces@2024-06-01-preview' = {
  parent: service
  name: 'default'
  properties: {
    title: 'Default'
    description: 'Default workspace for agent-framework-demo'
  }
}

resource env 'Microsoft.ApiCenter/services/workspaces/environments@2024-06-01-preview' = {
  parent: workspace
  name: 'local-dev'
  properties: {
    title: 'Local dev'
    kind: 'development'
    description: 'Developer laptop, MCP server on localhost.'
  }
}

resource api 'Microsoft.ApiCenter/services/workspaces/apis@2024-06-01-preview' = {
  parent: workspace
  name: 'internal-apis'
  properties: {
    title: 'Internal APIs (MCP)'
    kind: 'mcp'
    description: 'Insurance internal-API tools exposed as an MCP server (Lab 8).'
    summary: 'Three insurance-domain tools: get_policy, search_guidelines, get_loss_runs.'
    contacts: [
      {
        name: 'Platform team'
        email: 'platform@example.com'
      }
    ]
  }
}

resource version 'Microsoft.ApiCenter/services/workspaces/apis/versions@2024-06-01-preview' = {
  parent: api
  name: 'v1'
  properties: {
    title: 'v1'
    lifecycleStage: 'production'
  }
}

resource definition 'Microsoft.ApiCenter/services/workspaces/apis/versions/definitions@2024-06-01-preview' = {
  parent: version
  name: 'mcp-default'
  properties: {
    title: 'MCP tool catalog'
    description: 'Tools exposed by the internal-apis MCP server.'
  }
}

resource deployment 'Microsoft.ApiCenter/services/workspaces/apis/deployments@2024-06-01-preview' = {
  parent: api
  name: 'local'
  properties: {
    title: 'Local dev deployment'
    description: 'mcp_server.py running on the developer laptop.'
    environmentId: '/workspaces/${workspace.name}/environments/${env.name}'
    definitionId: '/workspaces/${workspace.name}/apis/${api.name}/versions/${version.name}/definitions/${definition.name}'
    server: {
      runtimeUri: [
        mcpRuntimeUri
      ]
    }
    state: 'active'
  }
}

output apiCenterName string = service.name
output portalUri string = 'https://portal.azure.com/#@/resource${service.id}'
