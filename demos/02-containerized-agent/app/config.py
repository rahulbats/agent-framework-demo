"""
Agent configuration from environment variables.
In Foundry Agent Service, these are injected automatically.
"""

import os


class AgentConfig:
    # Azure OpenAI / Foundry LLM
    AZURE_OPENAI_ENDPOINT: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_VERSION: str = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    # Agent Service
    AGENT_PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    AGENT_VERSION: str = os.environ.get("AGENT_VERSION", "v1")

    # Application Insights (auto-injected by Foundry)
    APPLICATIONINSIGHTS_CONNECTION_STRING: str = os.environ.get(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
    )
