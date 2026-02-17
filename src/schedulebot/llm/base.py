"""Abstract base for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


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
