param apimName string = 'rahul-ai-gateway'
param location string = 'eastus2'
param publisherEmail string = 'rahulbhatt@microsoft.com'
param publisherName string = 'Rahul Bhatt'

resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: apimName
  location: location
  sku: {
    name: 'BasicV2'
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

output gatewayUrl string = 'https://${apim.name}.azure-api.net'
output principalId string = apim.identity.principalId
