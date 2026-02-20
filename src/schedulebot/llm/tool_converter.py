"""Convert Anthropic tool schemas to OpenAI function-calling format."""

from __future__ import annotations

from typing import Any


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-format tool definitions to OpenAI function-calling format.

    Anthropic format:
        {"name": "...", "description": "...", "input_schema": {...}}

    OpenAI format:
        {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    openai_tools = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools
