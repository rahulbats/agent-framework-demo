terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

# ============================================================
# Resource Group
# ============================================================

resource "azurerm_resource_group" "agents" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# ============================================================
# Azure OpenAI (for LLM)
# ============================================================

resource "azurerm_cognitive_account" "openai" {
  name                = "${var.prefix}-openai"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  kind                = "OpenAI"
  sku_name            = "S0"
  tags                = var.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "gpt4o" {
  name                 = "gpt-4o"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-08-06"
  }

  sku {
    name     = "Standard"
    capacity = 30
  }
}

# ============================================================
# Application Insights (Observability)
# ============================================================

resource "azurerm_log_analytics_workspace" "agents" {
  name                = "${var.prefix}-logs"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  sku                 = "PerGB2018"
  retention_in_days   = 90
  tags                = var.tags
}

resource "azurerm_application_insights" "agents" {
  name                = "${var.prefix}-appinsights"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  workspace_id        = azurerm_log_analytics_workspace.agents.id
  application_type    = "web"
  tags                = var.tags
}

# ============================================================
# Azure API Management (LLM Gateway)
# ============================================================

resource "azurerm_api_management" "gateway" {
  name                = "${var.prefix}-apim"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  publisher_name      = var.apim_publisher_name
  publisher_email     = var.apim_publisher_email
  sku_name            = "Developer_1"
  tags                = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# APIM → OpenAI backend
resource "azurerm_api_management_backend" "openai" {
  name                = "openai-backend"
  resource_group_name = azurerm_resource_group.agents.name
  api_management_name = azurerm_api_management.gateway.name
  protocol            = "http"
  url                 = "${azurerm_cognitive_account.openai.endpoint}openai"

  credentials {
    header = {
      "api-key" = azurerm_cognitive_account.openai.primary_access_key
    }
  }
}

# ============================================================
# Azure AI Content Safety (Guardrails)
# ============================================================

resource "azurerm_cognitive_account" "content_safety" {
  name                = "${var.prefix}-content-safety"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  kind                = "ContentSafety"
  sku_name            = "S0"
  tags                = var.tags
}

# ============================================================
# Azure Container Registry (Agent Containers)
# ============================================================

resource "azurerm_container_registry" "agents" {
  name                = "${replace(var.prefix, "-", "")}acr"
  location            = azurerm_resource_group.agents.location
  resource_group_name = azurerm_resource_group.agents.name
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}

# ============================================================
# Key Vault (Secrets Management)
# ============================================================

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "agents" {
  name                       = "${var.prefix}-kv"
  location                   = azurerm_resource_group.agents.location
  resource_group_name        = azurerm_resource_group.agents.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  enable_rbac_authorization  = true
  tags                       = var.tags
}
