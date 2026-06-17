"""LLM Provider — Featherless (primary) / OpenAI (fallback).

Resolves the provider chain from environment variables and exposes a unified
``chat_completion`` interface for all agents.

Provider chain:
  1. ``FEATHERLESS_API_KEY`` (or ``FEATHERLESS_KEY``) → Featherless AI
  2. ``OPENAI_API_KEY`` → OpenAI
  3. None — returns ``None`` (caller handles fallback)

Usage::

    from agents.provider import provider

    text = await provider.chat_completion(system_prompt, user_prompt)
    if text is None:
        ...  # handle no-provider fallback
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

from protocols.json_parser import parse_llm_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

FEATHERLESS_BASE = "https://api.featherless.ai/v1"
OPENAI_BASE = "https://api.openai.com/v1"
AIML_BASE = "https://api.aimlapi.com/v1"

DEFAULT_FEATHERLESS_MODEL = "Qwen/Qwen2.5-14B-Instruct"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_AIML_MODEL = "x-ai/grok-4-1-fast-reasoning"


@dataclass
class ProviderConfig:
    """Resolved provider configuration.

    Created by :func:`resolve_config` — callers should not instantiate directly.
    """

    api_key: str
    base_url: str
    model: str
    provider_name: str = "featherless"


def resolve_config(preference: str | None = None) -> ProviderConfig | None:
    """Resolve the active provider from environment variables.

    Returns:
        A ``ProviderConfig`` for the first available provider, or ``None``
        when no API key is configured.
    """
    def get_featherless():
        key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("FEATHERLESS_KEY")
        if key:
            model = os.getenv("FEATHERLESS_MODEL", DEFAULT_FEATHERLESS_MODEL)
            return ProviderConfig(
                api_key=key,
                base_url=f"{FEATHERLESS_BASE}/chat/completions",
                model=model,
                provider_name="featherless",
            )
        return None

    def get_aiml():
        key = os.getenv("AIML_API_KEY") or os.getenv("AIMLAPI_API_KEY")
        if key:
            model = os.getenv("AIML_MODEL", DEFAULT_AIML_MODEL)
            return ProviderConfig(
                api_key=key,
                base_url=f"{AIML_BASE}/chat/completions",
                model=model,
                provider_name="aiml",
            )
        return None

    def get_openai():
        key = os.getenv("OPENAI_API_KEY")
        if key:
            model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
            return ProviderConfig(
                api_key=key,
                base_url=f"{OPENAI_BASE}/chat/completions",
                model=model,
                provider_name="openai",
            )
        return None

    # 1. Try preference first
    if preference == "featherless":
        conf = get_featherless()
        if conf:
            return conf
    elif preference == "aiml":
        conf = get_aiml()
        if conf:
            return conf
    elif preference == "openai":
        conf = get_openai()
        if conf:
            return conf

    # 2. Try default priority chain
    conf = get_featherless()
    if conf:
        return conf
    conf = get_aiml()
    if conf:
        return conf
    conf = get_openai()
    if conf:
        return conf

    return None


# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_LLM_CLIENT: httpx.AsyncClient | None = None


def _get_client(timeout: float = 30.0) -> httpx.AsyncClient:
    global _LLM_CLIENT
    if _LLM_CLIENT is None or _LLM_CLIENT.is_closed:
        _LLM_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    return _LLM_CLIENT


async def close_client() -> None:
    """Close the shared HTTP client. Idempotent."""
    global _LLM_CLIENT
    if _LLM_CLIENT is not None and not _LLM_CLIENT.is_closed:
        await _LLM_CLIENT.aclose()
    _LLM_CLIENT = None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider:
    """LLM provider wrapper.

    Typical usage uses the module-level singleton ``provider``, but you may
    also instantiate your own for custom settings::

        custom = Provider(timeout=60.0, max_retries=3)
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_delay: float = 1.5,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    def resolve(self, preference: str | None = None) -> ProviderConfig | None:
        """Resolve the active provider configuration.

        Returns ``None`` when no API key is found.
        """
        return resolve_config(preference)

    async def chat_completion(
        self,
        system: str,
        user: str,
        *,
        config: ProviderConfig | None = None,
        provider_name: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        parse_json: bool = False,
    ) -> str | dict | list | None:
        """Call the LLM and return the response.

        Args:
            system: System prompt.
            user: User message.
            config: Provider config (resolved automatically if omitted).
            provider_name: Request a specific provider ('aiml', 'featherless', 'openai').
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            parse_json: When ``True``, parse the response as JSON and return
                the parsed value instead of raw text.

        Returns:
            Raw response text (``parse_json=False``), a parsed JSON value
            (``parse_json=True``), or ``None`` when no provider is configured
            or all retries are exhausted.
        """
        if config is None:
            config = resolve_config(provider_name)

        if config is None:
            logger.info("No LLM provider configured — returning None")
            return None

        client = _get_client(self._timeout)

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await client.post(
                    config.base_url,
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("LLM returned 0 choices")
                raw = choices[0].get("message", {}).get("content", "")

                if parse_json:
                    parsed = parse_llm_json(raw)
                    if parsed is None:
                        raise ValueError(f"Unparseable JSON: {raw[:200]}")
                    return parsed

                return raw

            except Exception as e:
                logger.warning(
                    "LLM [%s] attempt %d/%d failed: %s",
                    config.provider_name,
                    attempt,
                    self._max_retries,
                    e,
                )
                last_exc = e
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay)

        logger.error("All LLM attempts exhausted: %s", last_exc)
        return None


# Module-level default provider
provider = Provider()
