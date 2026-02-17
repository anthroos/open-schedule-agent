"""Ollama (local) LLM provider."""

from __future__ import annotations

import json
import urllib.request

from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Local Ollama API integration. No extra dependencies."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
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
            data = json.loads(resp.read())

        return data.get("message", {}).get("content", "")
