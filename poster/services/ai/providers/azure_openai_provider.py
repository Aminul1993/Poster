"""Azure OpenAI: a deployment's /chat/completions on an Azure OpenAI (or AI Foundry) resource.

Azure names deployments, not models: the deployment decides the model, so it is what Poster calls the model.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

from poster.config import API
from poster.errors import ServiceError
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.ai.text_provider import Message, OpenAICompatibleProvider


class AzureOpenAIProvider(OpenAICompatibleProvider):
    name = "azure"
    label = "Azure OpenAI"
    service = "Azure OpenAI"
    docs_url = "https://learn.microsoft.com/azure/ai-foundry/openai/reference"
    fields = (
        Field("endpoint", "Endpoint", "url", required=True, env="AZURE_OPENAI_ENDPOINT",
              placeholder="https://my-resource.openai.azure.com"),
        Field("deployment", "Deployment name", required=True, env="AZURE_OPENAI_DEPLOYMENT", placeholder="gpt-4o-captions",
              help="The deployment of a model that accepts images."),
        Field("api_version", "API version", env="AZURE_OPENAI_API_VERSION", default=API["text_ai"]["azure_api_version"]),
        Field("api_key", "API key", "secret", required=True, env="AZURE_OPENAI_API_KEY"),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The deployment is the model: any other name the browser sends means this deployment.
        self.default_model = self.values.get("deployment", "")
        self.allowed_models = ()

    def check_config(self) -> str | None:
        problem = super().check_config()
        if problem:
            return problem
        if not re.fullmatch(r"[\w.\-]{1,64}", self.values.get("deployment", "")):
            return "Deployment name contains only letters, numbers and . - _ characters."
        return None

    def chat_url(self, model: str) -> str:
        endpoint = self.values.get("endpoint", "").rstrip("/")
        version = self.values.get("api_version") or API["text_ai"]["azure_api_version"]
        deployment = quote(self.values.get("deployment", ""), safe="")
        return f"{endpoint}/openai/deployments/{deployment}/chat/completions?{urlencode({'api-version': version})}"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json", "api-key": self.api_key}

    def body(self, messages: list[Message], model: str) -> dict[str, Any]:
        body = super().body(messages, model)
        del body["model"]  # the URL's deployment decides
        return body

    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        return super().complete(messages, self.default_model, on_retry)

    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        raise ServiceError("Azure OpenAI deployments are not listed with an API key.", kind="not_found", service=self.service)

    def list_models(self, on_retry: RetryCallback) -> list[str]:
        self.require_configured()
        return [self.default_model]

    def test_connection(self, model: str, on_retry: RetryCallback) -> dict[str, Any]:
        """Azure has no model list for an API key, so the test asks the deployment for a few tokens: that checks
        the endpoint, the deployment, the API version and the key together."""
        self.require_configured()
        body = {"messages": [{"role": "user", "content": "Reply with OK."}], "max_completion_tokens": 16, "stream": False}
        self.post_json(self.chat_url(self.default_model), body, self.headers(), on_retry, timeout=API["text_ai"]["test_timeout"])
        return {"models": [self.default_model], "modelAvailable": True, "host": self.host, "provider": self.name,
                "label": self.label}
