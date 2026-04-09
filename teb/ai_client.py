"""
Unified AI client that supports both Anthropic (Claude) and OpenAI.

Usage:
    response_text = ai_chat(system_prompt, user_prompt, json_mode=True)

Provider is selected automatically based on config.get_ai_provider().
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from teb import config

logger = logging.getLogger(__name__)

_MAX_AI_RETRIES = 3
_AI_RETRY_BASE_DELAY = 2.0  # seconds


def _should_retry_ai(exc: Exception) -> bool:
    """Check if an AI API error is transient and should be retried."""
    err_str = str(exc).lower()
    # Rate limit or server errors
    if "429" in err_str or "rate limit" in err_str:
        return True
    if "500" in err_str or "502" in err_str or "503" in err_str or "overloaded" in err_str:
        return True
    if "timeout" in err_str or "connection" in err_str:
        return True
    return False


def ai_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """
    Send a chat message to the configured AI provider.

    Retries on transient errors (429, 5xx, timeouts) with exponential backoff.
    Falls back to secondary provider if primary fails after all retries.
    Raises RuntimeError if no AI provider is configured.
    """
    provider = config.get_ai_provider()
    if provider is None:
        raise RuntimeError("No AI provider configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)")

    # Determine primary and fallback providers
    if provider == "anthropic":
        primary_fn = _chat_anthropic
        fallback_fn = _chat_openai if config.OPENAI_API_KEY else None
    else:
        primary_fn = _chat_openai
        fallback_fn = _chat_anthropic if config.ANTHROPIC_API_KEY else None

    kwargs = dict(json_mode=json_mode, temperature=temperature, max_tokens=max_tokens)

    # Try primary provider with retries
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_AI_RETRIES):
        try:
            return primary_fn(system_prompt, user_prompt, **kwargs)
        except Exception as exc:
            last_exc = exc
            if _should_retry_ai(exc) and attempt < _MAX_AI_RETRIES - 1:
                delay = _AI_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("AI call failed (attempt %d/%d): %s. Retrying in %.1fs",
                               attempt + 1, _MAX_AI_RETRIES, exc, delay)
                time.sleep(delay)
                continue
            break

    # Try fallback provider if available
    if fallback_fn is not None:
        logger.info("Falling back to secondary AI provider after primary failure: %s", last_exc)
        try:
            return fallback_fn(system_prompt, user_prompt, **kwargs)
        except Exception as fallback_exc:
            logger.warning("Fallback AI provider also failed: %s", fallback_exc)

    # Re-raise the last primary exception
    raise last_exc  # type: ignore[misc]


def ai_chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Send a chat message expecting JSON back. Parses and returns a dict.

    Raises json.JSONDecodeError if the response is not valid JSON.
    """
    raw = ai_chat(system_prompt, user_prompt, json_mode=True,
                  temperature=temperature, max_tokens=max_tokens)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try stripping code fences one more time in case they weren't caught
        stripped = _strip_code_fences(raw)
        return json.loads(stripped)


def _chat_anthropic(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Call Anthropic Claude API."""
    from anthropic import Anthropic  # noqa: PLC0415

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Claude doesn't have a json_mode flag; we instruct in the system prompt
    sys = system_prompt
    if json_mode and "json" not in sys.lower():
        sys += "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no explanation, just JSON."

    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=sys,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text
    # Claude sometimes wraps JSON in ```json ... ```; strip that
    if json_mode:
        text = _strip_code_fences(text)
    return text


def _chat_openai(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Call OpenAI-compatible API."""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
    )

    kwargs: dict = {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or "{}"


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a string (```json ... ```)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        # Remove closing fence
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
    return stripped
