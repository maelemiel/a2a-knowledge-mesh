#!/usr/bin/env python3
"""Ultra-minimal test — one LLM fact extraction call."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.provider import Provider, resolve_config  # noqa: E402


async def main():
    config = resolve_config()
    if not config:
        print("❌ No provider")
        return

    provider = Provider(timeout=30.0, max_retries=1)
    content = Path(ROOT / "pyproject.toml").read_text()

    prompt = f"""Extract facts from this pyproject.toml as JSON.
Return a JSON array of objects with keys: subject, predicate, object, category.

Content:
```
{content}
```
Return ONLY the JSON array."""

    print(f"⏳ Calling {config.model}...")
    result = await provider.chat_completion(
        "You extract structured facts from code files. Return only JSON.",
        prompt,
        temperature=0.1,
        max_tokens=1500,
        parse_json=True,
    )

    if result:
        if isinstance(result, list):
            print(f"✅ {len(result)} facts:")
            for f in result[:10]:
                print(f"  {f}")
        elif isinstance(result, dict):
            print(f"✅ Dict response: {json.dumps(result, indent=2)[:500]}")
        else:
            print(f"✅ Response: {result}")
    else:
        print("❌ No response")


if __name__ == "__main__":
    asyncio.run(main())
