"""Unified LLM call interface across providers.

Each provider has a thin adapter that accepts the same arguments and returns
the same shape, so the UI layer never needs to know which provider it is
talking to.

Provider-specific quirks (SDK packages, parameter names, response shapes) are
isolated here. Adding a new provider means: install its SDK, add an adapter
function, and register it in PROVIDERS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Literal

Role = Literal["user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class ModelConfig:
    """One row from config/models.yaml after resolution."""

    id: str
    label: str
    provider: str
    model: str


class LLMError(Exception):
    """Wraps any provider error with a user-friendly message."""


# ---------- Provider adapters ----------

def _call_google(model: str, api_key: str, system: str, messages: list[Message]) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    # Gemini's chat history uses "user"/"model" roles, not "user"/"assistant".
    contents = [
        types.Content(
            role="model" if m.role == "assistant" else "user",
            parts=[types.Part.from_text(text=m.content)],
        )
        for m in messages
    ]

    config = types.GenerateContentConfig(system_instruction=system) if system else None
    response = client.models.generate_content(model=model, contents=contents, config=config)
    return response.text or ""


def _call_anthropic(model: str, api_key: str, system: str, messages: list[Message]) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        system=system or None,
        messages=[{"role": m.role, "content": m.content} for m in messages],
        max_tokens=4096,
    )
    # Concatenate any text blocks; non-text blocks are ignored for now.
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


def _call_openai(model: str, api_key: str, system: str, messages: list[Message]) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    payload = []
    if system:
        payload.append({"role": "system", "content": system})
    payload.extend({"role": m.role, "content": m.content} for m in messages)

    response = client.chat.completions.create(model=model, messages=payload)
    return response.choices[0].message.content or ""


PROVIDERS: dict[str, Callable[[str, str, str, list[Message]], str]] = {
    "google": _call_google,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
}

ENV_KEY_NAMES: dict[str, str] = {
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def get_api_key(provider: str, override: str | None = None) -> str | None:
    """Override (from UI) wins; otherwise fall back to the env var."""
    if override:
        return override.strip() or None
    return os.environ.get(ENV_KEY_NAMES.get(provider, ""), "").strip() or None


def chat(
    model_config: ModelConfig,
    system_prompt: str,
    messages: list[Message],
    api_key: str | None = None,
) -> str:
    """Send a chat request and return the assistant's reply text.

    Raises LLMError on any failure with a message safe to surface in the UI.
    """
    adapter = PROVIDERS.get(model_config.provider)
    if adapter is None:
        raise LLMError(f"Unknown provider: {model_config.provider}")

    key = get_api_key(model_config.provider, api_key)
    if not key:
        env_name = ENV_KEY_NAMES.get(model_config.provider, "?")
        raise LLMError(
            f"{model_config.provider} API 키가 없습니다. .env에 {env_name}를 설정하거나 사이드바에 입력하세요."
        )

    try:
        return adapter(model_config.model, key, system_prompt, messages)
    except Exception as e:
        raise LLMError(f"{model_config.provider} 호출 실패: {e}") from e
