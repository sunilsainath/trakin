"""Google Gemini provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.gateway import BaseChatProvider, ChatRequest

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Role names differ from the OpenAI/Anthropic convention.
_ROLE_MAP = {"user": "user", "assistant": "model"}


class GeminiChatProvider(BaseChatProvider):
    key = "gemini"
    display_name = "Google Gemini"

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key.get_secret_value())

    async def _complete(self, request: ChatRequest) -> tuple[str, int, int, list[dict[str, Any]]]:
        key = self._settings.gemini_api_key.get_secret_value()
        model = request.model or self._settings.gemini_chat_model

        system_parts = [m.content for m in request.messages if m.role == "system"]
        contents = [
            {
                "role": _ROLE_MAP.get(m.role, "user"),
                "parts": [{"text": m.content}],
            }
            for m in request.messages
            if m.role in {"user", "assistant"}
        ]

        if not contents:
            contents = [{"role": "user", "parts": [{"text": "."}]}]

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in request.tools
                    ]
                }
            ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json=payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"gemini {response.status_code}")
            data = response.json()

        candidates = data.get("candidates") or [{}]
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)

        # Gemini returns tool calls as functionCall parts.
        tool_calls = [
            {"name": call.get("name"), "input": call.get("args")}
            for p in parts
            if (call := p.get("functionCall"))
        ]

        usage = data.get("usageMetadata") or {}
        return (
            text,
            int(usage.get("promptTokenCount") or 0),
            int(usage.get("candidatesTokenCount") or 0),
            tool_calls,
        )
