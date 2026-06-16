"""Unit tests for A2A Knowledge Mesh components (JSON parser, HMAC validation, and Regex fallbacks).

Runs with standard library unittest module.
"""

from __future__ import annotations

import json
import unittest
import os
import shutil
from pathlib import Path

# Configure env vars before imports
os.environ["A2A_HMAC_SECRET"] = "0123456789abcdef0123456789abcdef"

from protocols.json_parser import parse_llm_json
from agents.auth import sign_body, _verify_hmac

class TestLLMJsonParser(unittest.TestCase):
    """Test suite for the resilient LLM JSON parser."""

    def test_strict_json(self):
        content = '{"winner_id": 2, "reason": "pyproject.toml is authorative"}'
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 2, "reason": "pyproject.toml is authorative"})

    def test_markdown_fences(self):
        content = '```json\n{"winner_id": 1, "reason": "most recent"}\n```'
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 1, "reason": "most recent"})

        content_no_lang = '```\n{"winner_id": 1}\n```'
        result = parse_llm_json(content_no_lang)
        self.assertEqual(result, {"winner_id": 1})

    def test_trailing_commas(self):
        content = '{"winner_id": 2, "reason": "test",}'
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 2, "reason": "test"})

        content_array = '[1, 2, 3, ]'
        result = parse_llm_json(content_array)
        self.assertEqual(result, [1, 2, 3])

    def test_single_quotes_dirtyjson(self):
        content = "{'winner_id': 2, 'reason': 'single quotes'}"
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 2, "reason": "single quotes"})

    def test_outermost_json_regex(self):
        content = 'Sure, here is the JSON: {"winner_id": 1, "reason": "extracted"} and comments'
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 1, "reason": "extracted"})


class TestHMACSecurity(unittest.TestCase):
    """Test suite for HMAC request signing and verification."""

    def test_sign_and_verify(self):
        body = b'{"jsonrpc": "2.0", "method": "test"}'
        signature = sign_body(body)
        self.assertTrue(len(signature) > 0)
        self.assertTrue(_verify_hmac(body, signature))

    def test_invalid_signature(self):
        body = b'{"jsonrpc": "2.0", "method": "test"}'
        self.assertFalse(_verify_hmac(body, "invalid-sig"))

    def test_tampered_body(self):
        body = b'{"jsonrpc": "2.0", "method": "test"}'
        signature = sign_body(body)
        tampered_body = b'{"jsonrpc": "2.0", "method": "test", "params": {"hack": true}}'
        self.assertFalse(_verify_hmac(tampered_body, signature))


class TestWebhookRegexFallback(unittest.TestCase):
    """Test suite for natural language regex pattern matching for resolution webhook."""

    def _match_regex(self, content: str) -> int | None:
        import re
        match = re.search(r"resolve\s+with\s+fact\s+(\d+)", content, re.IGNORECASE)
        if not match:
            match = re.search(
                r"(?:prends?|choisis?|garde?|ok\s+pour|fact|fait)\s+(\d+)",
                content, re.IGNORECASE,
            )
        if match:
            return int(match.group(1))
        return None

    def test_regex_patterns_english(self):
        self.assertEqual(self._match_regex("resolve with fact 2"), 2)
        self.assertEqual(self._match_regex("Resolve with fact 1"), 1)
        self.assertEqual(self._match_regex("take fact 2"), 2)

    def test_regex_patterns_french(self):
        self.assertEqual(self._match_regex("Prends le fait 1"), 1)
        self.assertEqual(self._match_regex("garde fait 2"), 2)
        self.assertEqual(self._match_regex("choisis le fait 1"), 1)
        self.assertEqual(self._match_regex("ok pour le fait 2"), 2)
        self.assertEqual(self._match_regex("garde 1"), 1)


if __name__ == "__main__":
    unittest.main()
