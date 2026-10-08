"""OpenAI provider adapter.

The only file in the codebase that knows the OpenAI request shape. Adding a
vendor means adding a sibling module and one registration line in the registry.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.gateway import (
    BaseChatProvider,
    ChatRequest,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from app.core.config import Settings
from app.core.errors import IntegrationNotConfiguredError

BASE_URL = "https://api.openai.com/v1"


class OpenAIChatProvider(BaseChatProvider):
    key = "openai"
    display_name = "OpenAI"

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openai_api_key.get_secret_value())

    async def _complete(self, request: ChatRequest) -> tuple[str, int, int, list[dict[str, Any]]]:
        key = self._settings.openai_api_key.get_secret_value()
        model = request.model or self._settings.openai_chat_model

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            # An agent proposing an action is not the same as executing it; the
            # tool_choice setting keeps the model from assuming execution.
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"openai {response.status_code}")
            data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}

        return (
            str(message.get("content") or ""),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            list(message.get("tool_calls") or []),
        )


class OpenAIEmbeddingProvider(EmbeddingProvider):
    key = "openai_embedding"
    dimensions = 1536

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openai_api_key.get_secret_value())

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if not self.is_configured:
            raise IntegrationNotConfiguredError("openai")

        key = self._settings.openai_api_key.get_secret_value()
        model = request.model or self._settings.openai_embedding_model

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": request.texts},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"openai embeddings {response.status_code}")
            data = response.json()

        vectors = [item["embedding"] for item in data.get("data", [])]
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        cost = round(tokens / 1000 * 0.000020, 6)
        return EmbeddingResponse(vectors=vectors, model=model, tokens=tokens, cost_cents=cost)
