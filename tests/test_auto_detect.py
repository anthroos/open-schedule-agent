"""Tests for LLM auto-detection logic in _build_llm."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from schedulebot.cli import _build_llm


@dataclass
class FakeLLMConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-20250414"
    base_url: str | None = None


@dataclass
class FakeConfig:
    llm: FakeLLMConfig = None

    def __post_init__(self):
        if self.llm is None:
            self.llm = FakeLLMConfig()


class TestAutoDetectAnthropicToOpenAI:
    """When configured for Anthropic but only OpenAI key is set."""

    def test_switches_to_openai(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="anthropic", model="claude-haiku-4-20250414"))
        env = {"OPENAI_API_KEY": "sk-test"}
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                llm, _, _ = _build_llm(config)
        assert type(llm).__name__ == "OpenAIProvider"

    def test_adjusts_model_from_claude_to_gpt(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="anthropic", model="claude-sonnet-4-20250514"))
        env = {"OPENAI_API_KEY": "sk-test"}
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                llm, _, _ = _build_llm(config)
        assert llm.model == "gpt-4o-mini"


class TestAutoDetectOpenAIToAnthropic:
    """When configured for OpenAI but only Anthropic key is set."""

    def test_switches_to_anthropic(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="openai", model="gpt-4o-mini"))
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
                llm, _, _ = _build_llm(config)
        assert type(llm).__name__ == "AnthropicProvider"

    def test_adjusts_model_from_gpt_to_claude(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="openai", model="gpt-4o"))
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
                llm, _, _ = _build_llm(config)
        assert llm.model == "claude-haiku-4-20250414"


class TestNoAutoDetect:
    """When the configured provider's key is present — no switching."""

    def test_anthropic_stays_anthropic(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="anthropic", model="claude-haiku-4-20250414"))
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict("os.environ", env, clear=False):
            llm, _, _ = _build_llm(config)
        assert type(llm).__name__ == "AnthropicProvider"
        assert llm.model == "claude-haiku-4-20250414"

    def test_openai_stays_openai(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="openai", model="gpt-4o"))
        env = {"OPENAI_API_KEY": "sk-test"}
        with patch.dict("os.environ", env, clear=False):
            llm, _, _ = _build_llm(config)
        assert type(llm).__name__ == "OpenAIProvider"
        assert llm.model == "gpt-4o"


class TestOllamaProvider:
    """Ollama provider selection."""

    def test_ollama_created(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="ollama", model="llama3", base_url=None))
        llm, _, _ = _build_llm(config)
        assert type(llm).__name__ == "OllamaProvider"
        assert llm.model == "llama3"
        assert llm.base_url == "http://localhost:11434"

    def test_ollama_custom_base_url(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="ollama", model="mistral", base_url="http://gpu-server:11434"))
        llm, _, _ = _build_llm(config)
        assert llm.base_url == "http://gpu-server:11434"


class TestUnknownProvider:
    """Unknown provider raises ValueError."""

    def test_raises(self):
        config = FakeConfig(llm=FakeLLMConfig(provider="gemini", model="gemini-pro"))
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            _build_llm(config)
