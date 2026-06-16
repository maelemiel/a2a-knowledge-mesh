"""LLM Fact Extractor — Extract structured facts from unstructured text.

Uses a provider chain (Featherless → OpenAI → timestamp fallback) to
extract (subject, predicate, object) triples from arbitrary file content.

Exports:
    LlmFactExtractor — async class wrapping extraction logic.

CLI usage:
    python -m agents.llm_extractor <filepath>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (reused pattern from reconciler_band.py)
# ---------------------------------------------------------------------------


def _parse_llm_json(content: str) -> list | dict | None:
    """Parse LLM output — handles markdown fences, trailing commas, truncation.

    Returns a list (expected for fact extraction) or dict (other use cases)
    or None if parsing fails.
    """
    if not content:
        return None
    cleaned = content.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        import re

        fixed = re.sub(r",\s*}", "}", cleaned)
        fixed = re.sub(r",\s*]", "]", fixed)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


def _deduplicate_facts(facts: list[dict]) -> list[dict]:
    """Remove duplicate facts by (subject, predicate, object) identity."""
    seen: set[tuple[str, str, str]] = set()
    result: list[dict] = []
    for f in facts:
        key = (f.get("subject", ""), f.get("predicate", ""), f.get("object", ""))
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def _filter_empty_object(facts: list[dict]) -> list[dict]:
    """Remove facts where object is empty or None."""
    return [f for f in facts if f.get("object")]


def _postprocess_facts(facts: list[dict]) -> list[dict]:
    """Run post-processing pipeline: validate, deduplicate, filter empties."""
    valid: list[dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        if "subject" not in f or "predicate" not in f or "object" not in f:
            continue
        valid.append(
            {
                "subject": str(f.get("subject", "")),
                "predicate": str(f.get("predicate", "")),
                "object": str(f.get("object", "")),
            }
        )
    cleaned = _deduplicate_facts(valid)
    cleaned = _filter_empty_object(cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# System prompt for extraction
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = (
    "You are a precise data extraction assistant. "
    "Extract factual statements from the provided file content. "
    "Each fact is a (subject, predicate, object) triple. "
    "Return ONLY a JSON array of objects with keys: subject, predicate, object. "
    "No explanations, no markdown formatting."
)


def _build_extraction_prompt(filename: str, content: str) -> str:
    return (
        "Extract factual statements from the following file content.\n"
        "Each fact is a (subject, predicate, object) triple where:\n"
        "- subject: the entity being described (project name, component, system)\n"
        "- predicate: the attribute or relationship (uses, version, port, depends-on, requires)\n"
        "- object: the value (Python 3.11, port 8000, requests>=2.28)\n"
        "\n"
        "Return ONLY a JSON array. No explanations, no markdown.\n"
        '[{"subject": "...", "predicate": "...", "object": "..."}]\n'
        "\n"
        f"File: {filename}\n"
        "Content:\n"
        f"{content}"
    )


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _resolve_provider_api_key() -> tuple[str, str, str] | None:
    """Resolve provider (Featherless → OpenAI). Returns (api_key, base_url, model) or None."""
    featherless_key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("FEATHERLESS_KEY")
    if featherless_key:
        model = os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-14B-Instruct")
        return featherless_key, FEATHERLESS_URL, model

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return openai_key, OPENAI_URL, model

    return None


# ---------------------------------------------------------------------------
# LLM call with retries
# ---------------------------------------------------------------------------


async def _call_llm(system: str, user: str, *, max_retries: int = 2) -> str | None:
    """Call the LLM provider chain. Returns raw response text or None on fallback."""
    resolved = _resolve_provider_api_key()
    if not resolved:
        return None

    api_key, base_url, model = resolved

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                resp = await client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("LLM returned 0 choices")
                return choices[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning("LLM attempt %d/%d failed: %s", attempt, max_retries, e)
            last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(1.5)

    logger.error("All LLM attempts exhausted: %s", last_exc)
    return None


# ---------------------------------------------------------------------------
# LlmFactExtractor
# ---------------------------------------------------------------------------


class LlmFactExtractor:
    """Extract structured facts from unstructured text using an LLM.

    Provider chain: Featherless → OpenAI → fallback (timestamp-based empty result).
    """

    async def extract_from_file(self, filepath: str, content: str) -> list[dict]:
        """Send file content to LLM, return list of {subject, predicate, object, source_id}.

        Args:
            filepath: Path or filename for context in the prompt.
            content: Raw text content to extract facts from.

        Returns:
            List of fact dicts with keys: subject, predicate, object, source_id.
            Returns empty list on all-fallback.
        """
        filename = Path(filepath).name
        user_prompt = _build_extraction_prompt(filename, content)
        raw = await _call_llm(EXTRACTION_SYSTEM_PROMPT, user_prompt)

        # Fallback: no LLM available → return empty list
        if raw is None:
            logger.info("No LLM provider available, returning empty fact list for %s", filename)
            return []

        parsed = _parse_llm_json(raw)
        if parsed is None or not isinstance(parsed, list):
            logger.warning("LLM returned unparseable output for %s: %.200s", filename, raw)
            return []

        facts = _postprocess_facts(parsed)

        # Add source_id to each fact
        source_id = f"llm:{filename}:{int(time.time())}"
        for f in facts:
            f["source_id"] = source_id

        return facts

    async def extract_batch(self, files: list[tuple[str, str]]) -> list[list[dict]]:
        """Batch version — extract facts from multiple files at once.

        Args:
            files: List of (filepath, content) tuples.

        Returns:
            List of fact lists, one per input file, in the same order.
        """
        results: list[list[dict]] = []
        for filepath, content in files:
            facts = await self.extract_from_file(filepath, content)
            results.append(facts)
        return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _cli_main() -> None:
    """CLI entry point. Usage: python -m agents.llm_extractor <filepath>"""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m agents.llm_extractor <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    path = Path(filepath)
    if not path.exists():
        print(f"Error: file not found: {filepath}")
        sys.exit(1)

    content = path.read_text(encoding="utf-8", errors="replace")
    print(f"📄 Extracting facts from: {filepath} ({len(content)} chars)\n")

    extractor = LlmFactExtractor()
    facts = await extractor.extract_from_file(str(path), content)

    if not facts:
        print("No facts extracted.")
        return

    print(f"✅ {len(facts)} fact(s) extracted:\n")
    for i, f in enumerate(facts, 1):
        print(f"  {i}. ({f['subject']}, {f['predicate']}, {f['object']})")
    print()


def main() -> None:
    """Synchronous wrapper for CLI entry."""
    asyncio.run(_cli_main())


if __name__ == "__main__":
    main()
