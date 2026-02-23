"""OpenAI LLM provider."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..retry import retry_async
from .base import LLMProvider
from .tool_converter import anthropic_tools_to_openai
from .types import LLMToolResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI API integration with function-calling support."""

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
        response = await retry_async(
            self.client.chat.completions.create,
            model=self.model,
            messages=full_messages,
            max_tokens=2048,
            label="openai.chat",
        )
        return response.choices[0].message.content

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict[str, Any]],
    ) -> LLMToolResponse:
        """Chat with tool definitions using OpenAI function calling.

        Accepts messages in Anthropic format (tool_use/tool_result content blocks)
        and converts them to OpenAI format internally.
        """
        openai_tools = anthropic_tools_to_openai(tools)
        openai_messages = self._convert_messages(system_prompt, messages)

        response = await retry_async(
            self.client.chat.completions.create,
            model=self.model,
            messages=openai_messages,
            tools=openai_tools,
            max_tokens=2048,
            label="openai.chat_with_tools",
        )

        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=arguments,
                ))

        stop_reason = "tool_use" if tool_calls else "end_turn"

        return LLMToolResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _convert_messages(
        system_prompt: str, messages: list[dict]
    ) -> list[dict]:
        """Convert Anthropic-format conversation to OpenAI chat format.

        Handles:
        - Simple {"role": "user"/"assistant", "content": "text"} → pass through
        - Assistant with tool_use content blocks → tool_calls array
        - User with tool_result content blocks → role="tool" messages
        """
        openai_msgs: list[dict] = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Simple text message
            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})
                continue

            # Content is a list of blocks (Anthropic format)
            if isinstance(content, list):
                if role == "assistant":
                    # Extract text + tool_use blocks
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"]),
                                },
                            })

                    assistant_msg: dict[str, Any] = {"role": "assistant"}
                    assistant_msg["content"] = " ".join(text_parts) if text_parts else None
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    openai_msgs.append(assistant_msg)

                elif role == "user":
                    # Check if this is tool_result blocks
                    tool_results = [b for b in content if b.get("type") == "tool_result"]
                    if tool_results:
                        for tr in tool_results:
                            openai_msgs.append({
                                "role": "tool",
                                "tool_call_id": tr["tool_use_id"],
                                "content": tr.get("content", ""),
                            })
                    else:
                        # Mixed content — extract text
                        text_parts = []
                        for block in content:
                            if isinstance(block, str):
                                text_parts.append(block)
                            elif block.get("type") == "text":
                                text_parts.append(block["text"])
                        openai_msgs.append({
                            "role": "user",
                            "content": " ".join(text_parts) if text_parts else str(content),
                        })

        return openai_msgs
