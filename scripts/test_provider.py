#!/usr/bin/env python3
"""Minimal LLM provider test — just verifies the API key works."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.provider import Provider, resolve_config  # noqa: E402


async def main():
    config = resolve_config()
    if config is None:
        print("❌ No LLM provider configured")
        return

    print(f"  Provider: {config.provider_name}")
    print(f"  Model: {config.model}")
    print(f"  Base URL: {config.base_url.split('/chat')[0]}")
    print(f"  API Key: {config.api_key[:8]}...{config.api_key[-4:]}")
    print()

    provider = Provider(timeout=15.0, max_retries=1)
    print("⏳ Calling LLM...")

    result = await provider.chat_completion(
        "You are a helpful assistant. Reply with ONLY the word 'OK' and nothing else.",
        "Say OK.",
        temperature=0.0,
        max_tokens=10,
    )

    if result:
        print(f"✅ LLM response: {result[:100]}")
    else:
        print("❌ No response from LLM")


if __name__ == "__main__":
    asyncio.run(main())
