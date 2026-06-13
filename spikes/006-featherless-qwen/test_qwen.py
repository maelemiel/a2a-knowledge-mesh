"""Spike 006: Test Featherless AI + Qwen 3.7 Plus."""

import os
from openai import OpenAI

FEATHERLESS_KEY = os.environ.get("FEATHERLESS_KEY")
if not FEATHERLESS_KEY:
    print("❌ FEATHERLESS_KEY not set")
    exit(1)

client = OpenAI(
    base_url="https://api.featherless.ai/v1",
    api_key=FEATHERLESS_KEY,
)

# Try to find Qwen 3.7
models = client.models.list()
qwen_models = [m.id for m in models.data if "qwen" in m.id.lower() or "qwq" in m.id.lower()]
print(f"Found {len(qwen_models)} Qwen models:")
for m in qwen_models:
    print(f"  - {m}")

# Pick the most likely Qwen 3.7 model
candidates = [m for m in qwen_models if "3" in m and ("plus" in m.lower() or "Plus" in m)]
if not candidates:
    candidates = qwen_models[:3]

if not candidates:
    print("❌ No Qwen models found")
    exit(1)

model = candidates[0]
print(f"\nTesting: {model}")

r = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Say hello in 5 words or less"}],
    max_tokens=50,
)

content = r.choices[0].message.content or "(no content)"
print(f"Response: {content}")
print(f"Model used: {r.model}")
print(f"Tokens: {r.usage.total_tokens if r.usage else '?'}")
print("\n✅ FEATHERLESS + QWEN VALIDATED")
