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
import logging
import os
import time
from pathlib import Path

from agents.provider import provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (reused pattern from reconciler_band.py)
# ---------------------------------------------------------------------------


from protocols.json_parser import parse_llm_json as _parse_llm_json


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
# LLM call (delegates to shared provider)
# ---------------------------------------------------------------------------


async def _call_llm(system: str, user: str) -> str | None:
    """Call the LLM provider chain. Returns raw response text or None on fallback."""
    return await provider.chat_completion(
        system, user,
        temperature=0.1,
        max_tokens=2048,
    )


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
