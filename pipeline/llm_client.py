"""
llm_client.py — Configurable LLM wrapper.

Supports: openai | anthropic | google | groq
Configured via environment variables:
    LLM_PROVIDER=groq
    LLM_MODEL=llama-3.3-70b-versatile
    OPENAI_API_KEY=...
    ANTHROPIC_API_KEY=...
    GOOGLE_API_KEY=...
    GROQ_API_KEY=...
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
_MODEL = os.getenv("LLM_MODEL", "gpt-4o")


def _openai_complete(system: str, user: str, json_mode: bool) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    kwargs: dict = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    usage = response.usage
    if usage:
        from pipeline.utils.logger import get_logger

        log = get_logger("llm_client")
        log.debug(
            f"[OpenAI] prompt_tokens={usage.prompt_tokens} "
            f"completion_tokens={usage.completion_tokens}"
        )
    return response.choices[0].message.content or ""


def _anthropic_complete(system: str, user: str, json_mode: bool) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0,
    )
    return message.content[0].text if message.content else ""


def _groq_complete(system: str, user: str, json_mode: bool) -> str:
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    kwargs: dict = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def _google_complete(system: str, user: str, json_mode: bool) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    config_kwargs: dict = {"system_instruction": system, "temperature": 0}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    response = client.models.generate_content(
        model=_MODEL,
        contents=user,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text or ""


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
def complete(
    system: str,
    user: str,
    json_mode: bool = False,
    provider: Optional[str] = None,
) -> str:
    """
    Send a prompt to the configured LLM and return the text response.

    Args:
        system:    System/instruction prompt.
        user:      User message / content to process.
        json_mode: Ask the LLM to return valid JSON (supported by OpenAI natively).
        provider:  Override the configured provider for this call only.

    Returns:
        Raw string response from the LLM.
    """
    p = (provider or _PROVIDER).lower()
    if p == "openai":
        return _openai_complete(system, user, json_mode)
    elif p == "anthropic":
        return _anthropic_complete(system, user, json_mode)
    elif p == "groq":
        return _groq_complete(system, user, json_mode)
    elif p in ("google", "gemini"):
        return _google_complete(system, user, json_mode)
    else:
        raise ValueError(f"Unknown LLM provider: {p!r}. Set LLM_PROVIDER to openai, anthropic, groq, or google.")
