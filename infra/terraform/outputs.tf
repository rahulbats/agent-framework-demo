output "resource_group_name" {
  value = azurerm_resource_group.agents.name
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "openai_key" {
  value     = azurerm_cognitive_account.openai.primary_access_key
  sensitive = true
}

output "appinsights_connection_string" {
  value     = azurerm_application_insights.agents.connection_string
  sensitive = true
}

output "appinsights_instrumentation_key" {
  value     = azurerm_application_insights.agents.instrumentation_key
  sensitive = true
}

output "apim_gateway_url" {
  value = azurerm_api_management.gateway.gateway_url
}

output "content_safety_endpoint" {
  value = azurerm_cognitive_account.content_safety.endpoint
}

output "content_safety_key" {
  value     = azurerm_cognitive_account.content_safety.primary_access_key
  sensitive = true
}

output "acr_login_server" {
  value = azurerm_container_registry.agents.login_server
}

output "key_vault_uri" {
  value = azurerm_key_vault.agents.vault_uri
}
