"""Anthropic provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.gateway import BaseChatProvider, ChatRequest

BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
# Anthropic takes the system prompt out of band rather than in the messages list.
MAX_TOKENS_CEILING = 4096


class AnthropicChatProvider(BaseChatProvider):
    key = "anthropic"
    display_name = "Anthropic"

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.anthropic_api_key.get_secret_value())

    async def _complete(self, request: ChatRequest) -> tuple[str, int, int, list[dict[str, Any]]]:
        key = self._settings.anthropic_api_key.get_secret_value()
        model = request.model or self._settings.anthropic_chat_model

        system_parts = [m.content for m in request.messages if m.role == "system"]
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
            if m.role in {"user", "assistant"}
        ]

        if not messages:
            # A request with only a system prompt still needs a user turn.
            messages = [{"role": "user", "content": system_parts[0] if system_parts else "."}]

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": min(request.max_tokens, MAX_TOKENS_CEILING),
            "temperature": request.temperature,
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        if request.tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in request.tools
            ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"anthropic {response.status_code}")
            data = response.json()

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        tool_calls = [
            {"id": b.get("id"), "name": b.get("name"), "input": b.get("input")}
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}

        return (
            text,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            tool_calls,
        )
