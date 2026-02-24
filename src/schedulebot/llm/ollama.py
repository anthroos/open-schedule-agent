"""Ollama (local) LLM provider."""

from __future__ import annotations

import json
import logging
import urllib.request
from urllib.parse import urlparse

from ..retry import retry_async
from .base import LLMProvider

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


class OllamaProvider(LLMProvider):
    """Local Ollama API integration. No extra dependencies."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower()

        # Block known cloud metadata endpoints (SSRF protection)
        if hostname in _BLOCKED_HOSTS:
            raise ValueError(f"Ollama base_url points to a blocked host: {hostname}")

        # Warn about plaintext HTTP for non-local hosts
        is_local = hostname in ("localhost", "127.0.0.1", "::1", "")
        if not is_local and parsed.scheme == "http":
            logger.warning(
                "Ollama base_url uses HTTP for remote host '%s'. "
                "Conversation data (including PII) will be sent in plaintext. "
                "Consider using HTTPS.",
                hostname,
            )

        self.base_url = base_url.rstrip("/")

    async def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        def _call():
            payload = json.dumps({
                "model": self.model,
                "messages": full_messages,
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())

        data = await retry_async(_call, label="ollama.chat")
        return data.get("message", {}).get("content", "")
