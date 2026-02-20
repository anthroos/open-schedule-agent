"""Tests for OpenAI provider message conversion and tool integration."""

import json

import pytest

from schedulebot.llm.openai import OpenAIProvider


class TestConvertMessages:
    """Test _convert_messages: Anthropic format → OpenAI format."""

    def test_simple_text_messages(self):
        """Plain user/assistant messages pass through."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = OpenAIProvider._convert_messages("System prompt", messages)

        assert result[0] == {"role": "system", "content": "System prompt"}
        assert result[1] == {"role": "user", "content": "Hello"}
        assert result[2] == {"role": "assistant", "content": "Hi there"}

    def test_assistant_with_tool_use(self):
        """Assistant tool_use blocks → tool_calls array."""
        messages = [
            {"role": "user", "content": "Add Monday"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Adding it now."},
                    {
                        "type": "tool_use",
                        "id": "tc_1",
                        "name": "add_rule",
                        "input": {"day": "monday", "start": "10:00", "end": "18:00"},
                    },
                ],
            },
        ]
        result = OpenAIProvider._convert_messages("sys", messages)

        assistant_msg = result[2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Adding it now."
        assert len(assistant_msg["tool_calls"]) == 1
        tc = assistant_msg["tool_calls"][0]
        assert tc["id"] == "tc_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "add_rule"
        assert json.loads(tc["function"]["arguments"]) == {
            "day": "monday", "start": "10:00", "end": "18:00"
        }

    def test_tool_result_messages(self):
        """User tool_result blocks → individual role=tool messages."""
        messages = [
            {"role": "user", "content": "Do it"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc_1", "name": "add_rule",
                     "input": {"day": "monday", "start": "10:00", "end": "18:00"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_1",
                     "content": "Added rule #1: monday 10:00-18:00"},
                ],
            },
        ]
        result = OpenAIProvider._convert_messages("sys", messages)

        # Should have: system, user, assistant(with tool_calls), tool
        assert len(result) == 4
        tool_msg = result[3]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc_1"
        assert "Added rule" in tool_msg["content"]

    def test_multiple_tool_results(self):
        """Multiple tool_result blocks → multiple role=tool messages."""
        messages = [
            {"role": "user", "content": "Add two rules"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc_1", "name": "add_rule",
                     "input": {"day": "monday", "start": "10:00", "end": "12:00"}},
                    {"type": "tool_use", "id": "tc_2", "name": "add_rule",
                     "input": {"day": "tuesday", "start": "14:00", "end": "16:00"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_1", "content": "Done 1"},
                    {"type": "tool_result", "tool_use_id": "tc_2", "content": "Done 2"},
                ],
            },
        ]
        result = OpenAIProvider._convert_messages("sys", messages)

        # system + user + assistant + tool + tool
        assert len(result) == 5
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "tc_1"
        assert result[4]["role"] == "tool"
        assert result[4]["tool_call_id"] == "tc_2"

    def test_assistant_tool_use_no_text(self):
        """Assistant with tool_use but no text → content=None."""
        messages = [
            {"role": "user", "content": "Add Monday"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc_1", "name": "add_rule",
                     "input": {"day": "monday", "start": "10:00", "end": "18:00"}},
                ],
            },
        ]
        result = OpenAIProvider._convert_messages("sys", messages)

        assistant_msg = result[2]
        assert assistant_msg["content"] is None
        assert len(assistant_msg["tool_calls"]) == 1

    def test_full_conversation_round_trip(self):
        """Full tool-use cycle: user → assistant(tool) → tool_result → assistant(text)."""
        messages = [
            {"role": "user", "content": "Block Saturday"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc_1", "name": "block_time",
                     "input": {"day": "saturday", "start": "00:00", "end": "23:59"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_1",
                     "content": "Blocked saturday 00:00-23:59"},
                ],
            },
            {"role": "assistant", "content": "Saturday is now blocked all day."},
        ]
        result = OpenAIProvider._convert_messages("sys", messages)

        assert len(result) == 5  # system + user + assistant(tool) + tool + assistant(text)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert "tool_calls" in result[2]
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "assistant"
        assert result[4]["content"] == "Saturday is now blocked all day."
