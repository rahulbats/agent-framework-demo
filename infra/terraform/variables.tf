variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus2"
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
  default     = "demo-agents-rg"
}

variable "prefix" {
  description = "Naming prefix for all resources"
  type        = string
  default     = "demo-agents"
}

variable "tags" {
  description = "Tags for all resources"
  type        = map(string)
  default = {
    project     = "agent-framework-demo"
    environment = "demo"
    team        = "Platform"
  }
}

variable "apim_publisher_name" {
  description = "APIM publisher name"
  type        = string
  default     = "Platform Team"
}

variable "apim_publisher_email" {
  description = "APIM publisher email"
  type        = string
  default     = "platform@example.com"
}
