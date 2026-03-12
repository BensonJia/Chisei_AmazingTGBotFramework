from __future__ import annotations

import json
from collections.abc import AsyncIterator
from collections.abc import Iterator
from typing import Any

import httpx

from app.config_loader import LLMConfig


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=config.timeout_seconds)
        self._sync_client = httpx.Client(timeout=config.timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()
        self._sync_client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.bearer_token}",
            "Content-Type": "application/json",
        }

    async def complete(
        self, messages: list[dict[str, str]], stream: bool | None = None
    ) -> str:
        use_stream = self.config.stream if stream is None else stream
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": use_stream,
        }
        if not use_stream:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

        parts: list[str] = []
        async with self._client.stream(
            "POST", url, headers=self._headers(), json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    parts.append(text)
        return "".join(parts).strip()

    async def stream_chunks(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        async with self._client.stream(
            "POST", url, headers=self._headers(), json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    yield str(text)

    def complete_sync(
        self, messages: list[dict[str, str]], stream: bool | None = None
    ) -> str:
        use_stream = self.config.stream if stream is None else stream
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": use_stream,
        }
        if not use_stream:
            resp = self._sync_client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

        parts: list[str] = []
        with self._sync_client.stream(
            "POST", url, headers=self._headers(), json=payload
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    parts.append(text)
        return "".join(parts).strip()

    def stream_sync_chunks(self, messages: list[dict[str, str]]) -> Iterator[str]:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        with self._sync_client.stream(
            "POST", url, headers=self._headers(), json=payload
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    yield str(text)
