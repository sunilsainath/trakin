"""Self-hosted / OpenAI-compatible provider (vLLM, Ollama, llama.cpp, TGI).

Targets deployments where data residency or cost rules out a hosted API. The
adapter speaks the OpenAI wire format because every self-hosted server exposes
it, so no vendor-specific code is needed here either.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.gateway import BaseChatProvider, ChatRequest

FALLBACK_BASE_URL = "http://localhost:8080/v1"
FALLBACK_MODEL = "llama-3.1-70b"


class LocalChatProvider(BaseChatProvider):
    key = "local"
    display_name = "Self-hosted"

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.local_llm_base_url)

    async def _complete(self, request: ChatRequest) -> tuple[str, int, int, list[dict[str, Any]]]:
        base = (self._settings.local_llm_base_url or FALLBACK_BASE_URL).rstrip("/")
        model = request.model or FALLBACK_MODEL

        headers = {"Content-Type": "application/json"}
        api_key = self._settings.local_llm_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

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

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"local llm {response.status_code}")
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
