"""Shared types for LLM providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool invocation from the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMToolResponse:
    """Response from chat_with_tools: text + tool calls."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
