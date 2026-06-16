"""Resilient JSON parser for LLM outputs."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_llm_json(content: str) -> dict | list | None:
    """Parse LLM output with multiple fallback strategies.

    Strategy chain:
    1. Strip markdown fences → strict json.loads
    2. Trailing comma fix → json.loads
    3. dirtyjson (handles single quotes, trailing commas, missing quotes)
    4. Regex extraction of outermost JSON object/array
    5. Return None
    """
    if not content:
        return None

    cleaned = _strip_markdown_fences(content)

    # 1. Strict
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Trailing commas
    try:
        fixed = re.sub(r",\s*}", "}", cleaned)
        fixed = re.sub(r",\s*\]", "]", fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 3. dirtyjson — handles single quotes, unquoted keys, etc.
    try:
        import dirtyjson
        val = dirtyjson.loads(cleaned)
        if hasattr(val, "to_python"):
            return val.to_python()
        # convert to plain python dict/list if it's dirtyjson structures
        if isinstance(val, (dict, list)):
            return val
        return json.loads(json.dumps(val))
    except Exception as e:
        logger.debug("dirtyjson parsing failed: %s", e)

    # 4. Regex extraction — find outermost {} or []
    match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            try:
                fixed = re.sub(r",\s*}", "}", match.group(1))
                fixed = re.sub(r",\s*\]", "]", fixed)
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

    return None


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()
