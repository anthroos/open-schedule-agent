"""Anthropic (Claude) LLM provider."""

from __future__ import annotations

import os

from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Claude API integration."""

    def __init__(self, model: str = "claude-haiku-4-20250414", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    @property
    def client(self):
        if not self._client:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install schedulebot[anthropic]"
                )
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    async def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
