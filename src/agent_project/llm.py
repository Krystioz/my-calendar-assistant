"""Minimal live and mock model clients. Build your agent around this interface."""

import base64
import mimetypes
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from openai import OpenAI

# Chat-template tokens that some providers leak instead of a parsed tool call.
_TEMPLATE_MARKERS = ("<|channel>", "<tool_call|>", "<|tool_call>", "<end_of_turn>")
_SUMMED_USAGE = ("prompt_tokens", "completion_tokens", "total_tokens", "cost")


class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def image_part(path: str | Path) -> dict[str, Any]:
    """Content part for a local image, used next to a text part in one message."""
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def is_broken_reply(text: str | None, tool_calls: list | None) -> bool:
    """An empty reply or leaked template tokens is a provider failure."""
    if tool_calls:
        return False
    return not (text or "").strip() or any(m in text for m in _TEMPLATE_MARKERS)


class OpenRouterClient:
    def __init__(self, model: str | None = None, max_tokens: int = 1024) -> None:
        load_dotenv()
        api_key = os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("MODEL_NAME")
        self.max_tokens = max_tokens
        if not api_key or not self.model:
            raise ValueError("Set OPENROUTER_API_KEY and MODEL_NAME in .env")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            max_retries=1,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "extra_body": {"usage": {"include": True}},
        }
        if tools:
            request["tools"] = tools

        started = time.perf_counter()
        response = self.client.chat.completions.create(**request)
        usage = response.usage.model_dump() if response.usage else {}
        retried_after = None
        message = response.choices[0].message
        if is_broken_reply(message.content, message.tool_calls):
            # Retry once on another provider; the model itself did not fail.
            retried_after = getattr(response, "provider", None)
            if retried_after:
                request["extra_body"] = {
                    **request["extra_body"],
                    "provider": {"ignore": [retried_after]},
                }
            response = self.client.chat.completions.create(**request)
            message = response.choices[0].message
            retry_usage = response.usage.model_dump() if response.usage else {}
            for key in _SUMMED_USAGE:
                usage[key] = (usage.get(key) or 0) + (retry_usage.get(key) or 0)

        return {
            "text": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                }
                for call in message.tool_calls or []
            ],
            "finish_reason": response.choices[0].finish_reason,
            "model": response.model,
            "provider": getattr(response, "provider", None),
            "retried_after": retried_after,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": usage,
        }


class MockClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = deque(responses)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del messages, tools
        if not self.responses:
            raise RuntimeError("Mock response queue is empty")
        return self.responses.popleft()
