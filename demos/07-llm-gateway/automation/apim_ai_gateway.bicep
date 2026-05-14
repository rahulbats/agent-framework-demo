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

var policyXml = '<policies>\n  <inbound>\n    <base />\n    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />\n    <llm-semantic-cache-lookup score-threshold="0.05" embeddings-backend-id="aoai-embeddings-backend" embeddings-backend-auth="system-assigned">\n      <vary-by>@(context.Subscription.Id)</vary-by>\n    </llm-semantic-cache-lookup>\n    <llm-token-limit tokens-per-minute="${tpmPerKey}" counter-key="@(context.Subscription.Id)" estimate-prompt-tokens="true" tokens-consumed-header-name="x-tokens-consumed" remaining-tokens-header-name="x-ratelimit-remaining-tokens" />\n    <llm-emit-token-metric namespace="AzureOpenAI">\n      <dimension name="Subscription" value="@(context.Subscription.Name)" />\n      <dimension name="Agent" value="@(context.Request.Headers.GetValueOrDefault(&quot;x-agent-name&quot;, &quot;unknown&quot;))" />\n      <dimension name="Session" value="@(context.Request.Headers.GetValueOrDefault(&quot;x-session-id&quot;, &quot;unknown&quot;))" />\n    </llm-emit-token-metric>\n    <set-backend-service backend-id="aoai-backend" />\n  </inbound>\n  <backend>\n    <base />\n  </backend>\n  <outbound>\n    <base />\n    <llm-semantic-cache-store duration="3600" />\n  </outbound>\n  <on-error>\n    <base />\n  </on-error>\n</policies>'

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
