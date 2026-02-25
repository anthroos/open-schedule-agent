"""Anthropic (Claude) LLM provider."""

from __future__ import annotations

import os

from ..retry import retry_async
from .base import LLMProvider
from .types import LLMToolResponse, ToolCall


class AnthropicProvider(LLMProvider):
    """Claude API integration with tool use support."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str | None = None):
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
        """Text-only chat (used by guest mode and backward compat)."""
        response = await retry_async(
            self.client.messages.create,
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
            label="anthropic.chat",
        )
        return response.content[0].text

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMToolResponse:
        """Chat with tool definitions. Returns text + any tool calls.

        The caller is responsible for executing tools and sending results
        back in a follow-up call if needed.
        """
        response = await retry_async(
            self.client.messages.create,
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
            tools=tools,
            label="anthropic.chat_with_tools",
        )

        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMToolResponse(
            text=" ".join(text_parts) if text_parts else "",
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
        )
