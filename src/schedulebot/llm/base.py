"""Abstract base for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import LLMToolResponse


class LLMProvider(ABC):
    """Base class for LLM backends (Anthropic, OpenAI, Ollama)."""

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> str:
        """Send messages to the LLM and get a response.

        Args:
            system_prompt: System instructions for the LLM.
            messages: Conversation history as [{"role": "user"|"assistant", "content": "..."}].

        Returns:
            The assistant's response text.
        """
        ...

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict[str, Any]],
    ) -> LLMToolResponse:
        """Chat with tool definitions. Returns text + any tool calls.

        Tools are in Anthropic format (input_schema). Providers convert internally.
        Override this method to enable function-calling for a provider.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support tool use")
