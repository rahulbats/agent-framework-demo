// APIM AI Gateway: Azure OpenAI backend with the recommended AI policies.
//
// Policies wired (in <inbound> order):
//   1. Authenticate to AOAI via APIM's system-assigned MI
//   2. Semantic cache lookup        (azure-openai-semantic-cache-lookup)
//   3. Token rate limit per sub key (azure-openai-token-limit)
//   4. Emit per-agent token metrics (azure-openai-emit-token-metric)
// And in <outbound>:
//   5. Semantic cache store         (azure-openai-semantic-cache-store)

param apimName string = 'rahul-ai-gateway'

@description('AOAI endpoint, e.g. https://rahul-agent-framework-demo.cognitiveservices.azure.com/')
param aoaiEndpoint string

@description('Tokens per minute per APIM subscription key.')
param tpmPerKey int = 1000

resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' existing = {
  name: apimName
}

resource backend 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  parent: apim
  name: 'aoai-backend'
  properties: {
    protocol: 'http'
    url: '${aoaiEndpoint}openai'
  }
}

resource embeddingsBackend 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  parent: apim
  name: 'aoai-embeddings-backend'
  properties: {
    protocol: 'http'
    url: '${aoaiEndpoint}openai'
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  parent: apim
  name: 'azure-openai'
  properties: {
    displayName: 'Azure OpenAI'
    path: 'openai'
    protocols: [ 'https' ]
    subscriptionRequired: true
    serviceUrl: '${aoaiEndpoint}openai'
  }
}

resource opChat 'Microsoft.ApiManagement/service/apis/operations@2023-05-01-preview' = {
  parent: api
  name: 'chat-completions'
  properties: {
    displayName: 'Chat Completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
    templateParameters: [
      { name: 'deployment-id', type: 'string', required: true }
    ]
  }
}

// Policy XML lives in a sibling file so it stays human-readable and
// indentable. We substitute the TPM placeholder at deploy time.
var policyXml = replace(
  loadTextContent('aoai_policy.xml'),
  '{{TPM_PER_KEY}}',
  string(tpmPerKey)
)

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: policyXml
  }
  dependsOn: [
    backend
    embeddingsBackend
  ]
}

resource product 'Microsoft.ApiManagement/service/products@2023-05-01-preview' = {
  parent: apim
  name: 'ai-gateway'
  properties: {
    displayName: 'AI Gateway'
    description: 'Governed access to AOAI through APIM AI Gateway policies.'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}

resource productApi 'Microsoft.ApiManagement/service/products/apis@2023-05-01-preview' = {
  parent: product
  name: api.name
}

resource sub 'Microsoft.ApiManagement/service/subscriptions@2023-05-01-preview' = {
  parent: apim
  name: 'demo-key'
  properties: {
    displayName: 'demo-key'
    scope: '/products/${product.id}'
    state: 'active'
  }
}

output gatewayUrl string = 'https://${apim.name}.azure-api.net'
output apiPath string = api.properties.path
output subscriptionName string = sub.name
