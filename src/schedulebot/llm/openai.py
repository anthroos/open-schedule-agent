"""OpenAI LLM provider."""

from __future__ import annotations

import os

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI API integration."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None

    @property
    def client(self):
        if not self._client:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install schedulebot[openai]"
                )
            self._client = openai.OpenAI(api_key=self.api_key)
        return self._client

    async def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        response = self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=512,
        )
        return response.choices[0].message.content
