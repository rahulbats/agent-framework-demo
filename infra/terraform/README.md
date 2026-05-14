# Agent Infrastructure — Terraform

Terraform modules for provisioning the Azure infrastructure required for the agent platform.

## Modules

| Module | Resources |
|--------|-----------|
| `main.tf` | Resource group, AI Foundry, APIM, App Insights, Content Safety |
| `variables.tf` | Input variables with project defaults |
| `outputs.tf` | Connection strings, endpoints, keys |

## Usage

```bash
cd infra/terraform
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

## Prerequisites

- Azure CLI authenticated (`az login`)
- Terraform >= 1.5
- Subscription with quota for Azure OpenAI in East US 2
