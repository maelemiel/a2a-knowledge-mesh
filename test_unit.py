"""Unit tests for A2A Knowledge Mesh components (JSON parser, HMAC validation, and Regex fallbacks).

Runs with standard library unittest module.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock
import os
import shutil
from pathlib import Path

from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse

# Configure env vars before imports
os.environ["A2A_HMAC_SECRET"] = "0123456789abcdef0123456789abcdef"

from protocols.json_parser import parse_llm_json
from agents.auth import sign_body, _verify_hmac, configure_auth, A2AAuthMiddleware

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

    def test_empty_or_none_content(self):
        self.assertIsNone(parse_llm_json(""))
        self.assertIsNone(parse_llm_json(None))

    def test_dirtyjson_non_dict_list(self):
        content = "'single-quoted-string'"
        result = parse_llm_json(content)
        self.assertEqual(result, "single-quoted-string")

    def test_regex_outermost_json_with_trailing_comma(self):
        content = 'The output is: {"winner_id": 1, "reason": "extracted",}'
        result = parse_llm_json(content)
        self.assertEqual(result, {"winner_id": 1, "reason": "extracted"})

    def test_regex_outermost_json_fails_completely(self):
        content = 'The output is: {"winner_id": 1, "reason": invalid_syntax}'
        result = parse_llm_json(content)
        self.assertIsNone(result)


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
        self.assertIsNone(self._match_regex("this matches nothing"))


class TestAuthMiddleware(unittest.TestCase):
    """Test suite for A2AAuthMiddleware using Starlette TestClient."""

    def setUp(self):
        configure_auth(
            master_token="test-master",
            registry_token="test-registry",
            keeper_token="test-keeper",
            reconciler_token="test-reconciler",
            hmac_secret="test-hmac-secret-1234567890abcdef"
        )

        async def dummy_endpoint(request):
            return JSONResponse({"status": "ok"})

        self.app = Starlette(
            routes=[
                Route("/health", dummy_endpoint),
                Route("/a2a", dummy_endpoint, methods=["POST"]),
            ]
        )
        self.app.add_middleware(A2AAuthMiddleware, agent_role="keeper")
        self.client = TestClient(self.app)

    def test_public_path(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_missing_auth_header(self):
        resp = self.client.post("/a2a", json={"test": "data"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("missing or malformed", resp.json()["error"]["message"])

    def test_malformed_auth_header(self):
        resp = self.client.post("/a2a", json={"test": "data"}, headers={"Authorization": "Invalid"})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token(self):
        resp = self.client.post(
            "/a2a",
            json={"test": "data"},
            headers={"Authorization": "Bearer wrong-token"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_missing_hmac_signature(self):
        resp = self.client.post(
            "/a2a",
            json={"test": "data"},
            headers={"Authorization": "Bearer test-keeper"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("missing x-a2a-signature", resp.json()["error"]["message"])

    def test_invalid_hmac_signature(self):
        resp = self.client.post(
            "/a2a",
            json={"test": "data"},
            headers={
                "Authorization": "Bearer test-keeper",
                "X-A2A-Signature": "wrong-signature"
            }
        )
        self.assertEqual(resp.status_code, 403)

    def test_valid_token_and_signature(self):
        body = b'{"test": "data"}'
        sig = sign_body(body)
        resp = self.client.post(
            "/a2a",
            content=body,
            headers={
                "Authorization": "Bearer test-keeper",
                "X-A2A-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_master_token_override(self):
        body = b'{"test": "data"}'
        sig = sign_body(body)
        resp = self.client.post(
            "/a2a",
            content=body,
            headers={
                "Authorization": "Bearer test-master",
                "X-A2A-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(resp.status_code, 200)

    def test_empty_hmac_secret(self):
        import agents.auth
        old_secret = agents.auth._HMAC_SECRET
        agents.auth._HMAC_SECRET = b""
        try:
            self.assertEqual(sign_body(b"test"), "")
            self.assertFalse(_verify_hmac(b"test", "sig"))
            resp = self.client.post(
                "/a2a",
                json={"test": "data"},
                headers={"Authorization": "Bearer test-keeper"}
            )
            self.assertEqual(resp.status_code, 200)
        finally:
            agents.auth._HMAC_SECRET = old_secret

    def test_middleware_empty_token(self):
        app = Starlette()
        mw = A2AAuthMiddleware(app, agent_role="nonexistent")
        self.assertEqual(mw._expected_token, "")




class TestAuthAsync(unittest.IsolatedAsyncioTestCase):
    """Test suite for async authentication and client operations."""

    def setUp(self):
        configure_auth(
            master_token="test-master",
            registry_token="test-registry",
            keeper_token="test-keeper",
            reconciler_token="test-reconciler",
            hmac_secret="test-hmac-secret-1234567890abcdef"
        )

    async def test_non_http_scope(self):
        mock_app = unittest.mock.AsyncMock()
        mw = A2AAuthMiddleware(mock_app, agent_role="keeper")
        scope = {"type": "websocket"}
        async def mock_receive(): pass
        async def mock_send(): pass
        
        await mw(scope, mock_receive, mock_send)
        mock_app.assert_called_once_with(scope, mock_receive, mock_send)

    @unittest.mock.patch("agents.auth.get_a2a_client")
    async def test_a2a_call_success(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client

        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {"jsonrpc": "2.0", "result": {"foo": "bar"}}
        mock_client.post.return_value = mock_response

        from agents.auth import a2a_call
        result = await a2a_call("http://mock-url", "some-method", {"param": "val"}, target_role="keeper")
        self.assertEqual(result, {"foo": "bar"})

    @unittest.mock.patch("agents.auth.get_a2a_client")
    async def test_a2a_call_error(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client

        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}}
        mock_client.post.return_value = mock_response

        from agents.auth import a2a_call
        with self.assertRaises(RuntimeError) as ctx:
            await a2a_call("http://mock-url", "some-method", {"param": "val"}, target_role="keeper")
        self.assertIn("Method not found", str(ctx.exception))

    @unittest.mock.patch("agents.auth.get_a2a_client")
    async def test_a2a_call_fallback_to_master(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client

        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {"jsonrpc": "2.0", "result": {"foo": "bar"}}
        mock_client.post.return_value = mock_response

        from agents.auth import a2a_call, _ROLE_TOKENS
        old_keeper_token = _ROLE_TOKENS.get("keeper", "")
        _ROLE_TOKENS["keeper"] = ""
        try:
            result = await a2a_call("http://mock-url", "some-method", {"param": "val"}, target_role="keeper")
            self.assertEqual(result, {"foo": "bar"})
            mock_client.post.assert_called_once()
            called_headers = mock_client.post.call_args[1]["headers"]
            self.assertEqual(called_headers["Authorization"], "Bearer test-master")
        finally:
            _ROLE_TOKENS["keeper"] = old_keeper_token

    async def test_buffered_receive_fallback(self):
        configure_auth(
            master_token="test-master",
            registry_token="test-registry",
            keeper_token="test-keeper",
            reconciler_token="test-reconciler",
            hmac_secret="test-hmac-secret-1234567890abcdef"
        )
        mock_app = unittest.mock.AsyncMock()
        mw = A2AAuthMiddleware(mock_app, agent_role="keeper")
        
        body = b'{"test": "data"}'
        sig = sign_body(body)
        
        scope = {
            "type": "http",
            "path": "/a2a",
            "headers": [
                (b"authorization", f"Bearer test-keeper".encode("utf-8")),
                (b"x-a2a-signature", sig.encode("utf-8")),
            ]
        }
        
        receive_calls = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"}
        ]
        
        async def mock_receive():
            return receive_calls.pop(0) if receive_calls else {"type": "http.disconnect"}
            
        mock_send = unittest.mock.AsyncMock()
            
        await mw(scope, mock_receive, mock_send)
        
        mock_app.assert_called_once()
        called_args, _ = mock_app.call_args
        buffered_receive = called_args[1]
        
        msg1 = await buffered_receive()
        self.assertEqual(msg1["body"], body)
        
        msg2 = await buffered_receive()
        self.assertEqual(msg2["type"], "http.disconnect")

    async def test_close_client(self):
        from agents.auth import get_a2a_client, close_a2a_client
        client1 = await get_a2a_client()
        self.assertFalse(client1.is_closed)
        await close_a2a_client()
        self.assertTrue(client1.is_closed)


if __name__ == "__main__":
    unittest.main()
