from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings


class LLMUnavailableError(RuntimeError):
    """The configured LLM could not be reached."""


class LLMResponseError(RuntimeError):
    """The LLM returned a response that does not match the required schema."""


class LLMClient(Protocol):
    def extract_context(self, message: str) -> dict[str, str]:
        """Return only taxonomy slot values explicitly extracted from a message."""


@dataclass(frozen=True)
class FakeLLMClient:
    response: dict[str, str]

    def extract_context(self, message: str) -> dict[str, str]:
        return dict(self.response)


@dataclass(frozen=True)
class AnthropicLLMClient:
    api_key: str
    model: str
    timeout_seconds: float

    def extract_context(self, message: str) -> dict[str, str]:
        payload = {
            "model": self.model,
            "max_tokens": 300,
            "temperature": 0,
            "system": (
                "Extract agricultural context from the user's English message. "
                'Return JSON only: {"crop": string|null, "region": string|null, '
                '"season": string|null, "stage": string|null, "topic": string|null}. '
                "Never invent values; use null when absent."
            ),
            "messages": [{"role": "user", "content": message}],
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LLMUnavailableError("The live LLM is unavailable") from exc
        try:
            text = str(body["content"][0]["text"]).strip()
            if text.startswith("```"):
                text = text.removeprefix("```json").removesuffix("```").strip()
            result = json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("The live LLM returned malformed context JSON") from exc
        if not isinstance(result, dict):
            raise LLMResponseError("The live LLM context must be a JSON object")
        allowed = {"crop", "region", "season", "stage", "topic"}
        if any(key not in allowed for key in result):
            raise LLMResponseError("The live LLM returned an unsupported context slot")
        if any(value is not None and not isinstance(value, str) for value in result.values()):
            raise LLMResponseError("The live LLM context values must be strings or null")
        return {key: value for key, value in result.items() if value}


def get_llm_client(settings: Settings) -> LLMClient | None:
    provider = settings.llm_provider.casefold()
    if provider in {"", "deterministic", "none"}:
        return None
    if provider == "fake":
        return FakeLLMClient(response=settings.llm_fake_response)
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMUnavailableError("ANTHROPIC_API_KEY is required for the live LLM")
        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise LLMUnavailableError(f"Unsupported LLM provider: {settings.llm_provider}")
