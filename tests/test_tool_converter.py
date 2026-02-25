"""Tests for Anthropic → OpenAI tool schema conversion."""

from schedulebot.llm.tool_converter import anthropic_tools_to_openai
from schedulebot.llm.tools import GUEST_TOOLS, OWNER_TOOLS


def test_single_tool_conversion():
    """Basic conversion: input_schema → parameters."""
    anthropic = [{
        "name": "greet",
        "description": "Say hello",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }]
    result = anthropic_tools_to_openai(anthropic)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    fn = result[0]["function"]
    assert fn["name"] == "greet"
    assert fn["description"] == "Say hello"
    assert fn["parameters"]["properties"]["name"]["type"] == "string"
    assert fn["parameters"]["required"] == ["name"]


def test_empty_tools():
    """Empty list in → empty list out."""
    assert anthropic_tools_to_openai([]) == []


def test_tool_without_description():
    """Missing description defaults to empty string."""
    result = anthropic_tools_to_openai([{
        "name": "noop",
        "input_schema": {"type": "object", "properties": {}},
    }])
    assert result[0]["function"]["description"] == ""


def test_guest_tools_conversion():
    """All GUEST_TOOLS convert without error."""
    result = anthropic_tools_to_openai(GUEST_TOOLS)
    assert len(result) == len(GUEST_TOOLS)
    names = {t["function"]["name"] for t in result}
    assert "collect_guest_info" in names
    assert "confirm_booking" in names

    # Verify nested array type preserved (attendee_emails)
    confirm = next(t for t in result if t["function"]["name"] == "confirm_booking")
    props = confirm["function"]["parameters"]["properties"]
    assert props["attendee_emails"]["type"] == "array"


def test_owner_tools_conversion():
    """All OWNER_TOOLS convert without error."""
    result = anthropic_tools_to_openai(OWNER_TOOLS)
    assert len(result) == len(OWNER_TOOLS)
    names = {t["function"]["name"] for t in result}
    assert names == {
        "add_rule", "delete_rule", "block_time", "clear_rules", "clear_all",
        "show_rules", "set_timezone", "show_bookings", "cancel_booking",
    }


def test_required_field_preserved():
    """Required fields carry over correctly."""
    anthropic = [{
        "name": "test",
        "description": "test",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        },
    }]
    result = anthropic_tools_to_openai(anthropic)
    assert result[0]["function"]["parameters"]["required"] == ["a"]
