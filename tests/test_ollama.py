"""Tests for Ollama LLM provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from schedulebot.llm.ollama import OllamaProvider


class TestOllamaInit:
    def test_defaults(self):
        provider = OllamaProvider()
        assert provider.model == "llama3"
        assert provider.base_url == "http://localhost:11434"

    def test_custom_values(self):
        provider = OllamaProvider(model="mistral", base_url="http://gpu:11434/")
        assert provider.model == "mistral"
        assert provider.base_url == "http://gpu:11434"  # trailing slash stripped

    def test_no_chat_with_tools(self):
        provider = OllamaProvider()
        with pytest.raises(NotImplementedError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                provider.chat_with_tools("sys", [], [])
            )


class TestOllamaChat:
    @pytest.mark.asyncio
    async def test_chat_sends_correct_request(self):
        provider = OllamaProvider(model="llama3", base_url="http://localhost:11434")

        fake_response = json.dumps({
            "message": {"content": "Hello! How can I help?"}
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            result = await provider.chat("You are helpful.", [{"role": "user", "content": "Hi"}])

        assert result == "Hello! How can I help?"
        mock_urlopen.assert_called_once()

        # Verify the request payload
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "http://localhost:11434/api/chat"
        body = json.loads(req.data)
        assert body["model"] == "llama3"
        assert body["stream"] is False
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_chat_handles_empty_response(self):
        provider = OllamaProvider()

        fake_response = json.dumps({}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await provider.chat("sys", [{"role": "user", "content": "test"}])

        assert result == ""
