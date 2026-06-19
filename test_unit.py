"""Unit tests for A2A Knowledge Mesh components (JSON parser, HMAC validation, and Regex fallbacks).

Runs with standard library unittest module.
"""

from __future__ import annotations

import unittest
import unittest.mock
import os
import asyncio
import json as jsonlib
import sqlite3
import tempfile
from pathlib import Path

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
    """Test suite for A2AAuthMiddleware using a small ASGI harness."""

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
        self.client = _ASGITestClient(A2AAuthMiddleware(self.app, agent_role="keeper"))

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


class _ASGIResponse:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def json(self):
        return jsonlib.loads(self._body.decode("utf-8"))


class _ASGITestClient:
    def __init__(self, app):
        self.app = app

    def get(self, path: str, headers: dict[str, str] | None = None) -> _ASGIResponse:
        return asyncio.run(self._request("GET", path, b"", headers or {}))

    def post(
        self,
        path: str,
        *,
        json: dict | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> _ASGIResponse:
        request_headers = dict(headers or {})
        if content is None and json is not None:
            content = jsonlib.dumps(json).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        return asyncio.run(self._request("POST", path, content or b"", request_headers))

    async def _request(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> _ASGIResponse:
        raw_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        receive_messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent: list[dict] = []

        async def receive():
            if receive_messages:
                return receive_messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await self.app(scope, receive, send)

        status_code = 500
        body_chunks = []
        for message in sent:
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
        return _ASGIResponse(status_code, b"".join(body_chunks))


class TestBridgeDashboardState(unittest.TestCase):
    """Dashboard counters should reflect current DB state, not old timeline text."""

    def test_resolved_current_pair_is_not_open_conflict(self):
        import agents.bridge_agent as bridge

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            keeper_db = tmpdir / "keeper.db"
            reconciler_db = tmpdir / "reconciler.db"

            conn = sqlite3.connect(keeper_db)
            conn.execute("""
                CREATE TABLE facts (
                    id INTEGER PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_id TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO facts VALUES (1, 'project-ALLY', 'framework', 'Next.js', 'docs')"
            )
            conn.execute(
                "INSERT INTO facts VALUES (2, 'project-ALLY', 'framework', 'FastAPI', 'code')"
            )
            conn.commit()
            conn.close()

            conn = sqlite3.connect(reconciler_db)
            conn.execute("""
                CREATE TABLE conflicts (
                    id TEXT PRIMARY KEY,
                    fact_a_id INTEGER NOT NULL,
                    fact_b_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            conn.execute("INSERT INTO conflicts VALUES ('abc12345', 1, 2, 'resolved')")
            conn.commit()
            conn.close()

            old_keeper, old_reconciler = bridge.KEEPER_DB, bridge.RECONCILER_DB
            bridge.KEEPER_DB = keeper_db
            bridge.RECONCILER_DB = reconciler_db
            try:
                state = bridge.get_mesh_state()
            finally:
                bridge.KEEPER_DB = old_keeper
                bridge.RECONCILER_DB = old_reconciler

            self.assertEqual(state["facts_stored"], 2)
            self.assertEqual(state["conflicts"], 0)
            self.assertEqual(state["resolved"], 1)

    def test_local_state_mirror_emits_fact_conflict_and_resolution_events(self):
        import agents.bridge_agent as bridge

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            keeper_db = tmpdir / "keeper.db"
            reconciler_db = tmpdir / "reconciler.db"

            conn = sqlite3.connect(keeper_db)
            conn.execute("""
                CREATE TABLE facts (
                    id INTEGER PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO facts VALUES "
                "(1, 'project-ALLY', 'framework', 'Next.js', 'docs', 100),"
                "(2, 'project-ALLY', 'framework', 'FastAPI', 'code', 101)"
            )
            conn.commit()
            conn.close()

            conn = sqlite3.connect(reconciler_db)
            conn.execute("""
                CREATE TABLE conflicts (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fact_a_id INTEGER NOT NULL,
                    fact_b_id INTEGER NOT NULL,
                    resolution_fact_id INTEGER,
                    resolution_reason TEXT,
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    severity TEXT,
                    score_confidence REAL,
                    auto_resolved INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()

            old_keeper, old_reconciler = bridge.KEEPER_DB, bridge.RECONCILER_DB
            bridge.KEEPER_DB = keeper_db
            bridge.RECONCILER_DB = reconciler_db
            try:
                mirror = bridge.LocalStateMirror()
                events = mirror.poll()
                contents = [event.content for event, _ in events]
                self.assertTrue(any("Fact stored #1" in c for c in contents))
                self.assertTrue(any("Fact stored #2" in c for c in contents))
                self.assertTrue(any("Conflict detected by Keeper" in c for c in contents))

                conn = sqlite3.connect(reconciler_db)
                conn.execute(
                    "INSERT INTO conflicts VALUES "
                    "('abc12345', 'project-ALLY', 'framework', 'open', 1, 2, "
                    "NULL, NULL, 102, NULL, 'MEDIUM', 0.70, 0)"
                )
                conn.commit()
                conn.close()

                events = mirror.poll()
                self.assertTrue(any(
                    "Reconciler opened conflict #abc12345" in event.content
                    for event, _ in events
                ))

                conn = sqlite3.connect(reconciler_db)
                conn.execute(
                    "UPDATE conflicts SET status='resolved', resolution_fact_id=2, "
                    "resolution_reason='code is source of truth', resolved_at=103 "
                    "WHERE id='abc12345'"
                )
                conn.commit()
                conn.close()

                events = mirror.poll()
                self.assertTrue(any(
                    "Conflict #abc12345 resolved -> fact #2" in event.content
                    for event, _ in events
                ))
            finally:
                bridge.KEEPER_DB = old_keeper
                bridge.RECONCILER_DB = old_reconciler

    def test_persistent_history_deduplicates_replayed_events(self):
        import agents.bridge_agent as bridge

        with tempfile.TemporaryDirectory() as tmp:
            old_bridge_db = bridge.BRIDGE_DB
            bridge.BRIDGE_DB = Path(tmp) / "bridge.db"
            try:
                event = bridge.Event(
                    "message",
                    "@Keeper detect",
                    sender_id="user-1",
                    sender_name="Eliott",
                    timestamp="2026-06-19T12:00:00+00:00",
                )
                bridge.append_history(event)
                bridge.append_history(event)
                history = bridge.get_history()
            finally:
                bridge.BRIDGE_DB = old_bridge_db

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["content"], "@Keeper detect")


class TestBandDemoSeed(unittest.TestCase):
    """The deterministic hackathon demo should always produce conflicts."""

    def test_demo_facts_generate_config_drift_conflicts(self):
        from agents.keeper import KeeperStore
        from agents.keeper_band import DEMO_FACTS

        with tempfile.TemporaryDirectory() as tmp:
            store = KeeperStore(str(Path(tmp) / "keeper.db"))
            try:
                store.store_batch(DEMO_FACTS)
                conflicts = store.detect_conflicts()
            finally:
                store.close()

        keys = {(c["subject"], c["predicate"]) for c in conflicts}
        self.assertIn(("runtime", "python-version"), keys)
        self.assertIn(("install", "package-manager"), keys)
        self.assertIn(("service-api", "auth-provider"), keys)
        self.assertGreaterEqual(len(conflicts), 3)


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
                (b"authorization", "Bearer test-keeper".encode("utf-8")),
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


class TestLlmProvider(unittest.IsolatedAsyncioTestCase):
    """Test suite for the LLM Provider wrapper."""

    def setUp(self):
        self.old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    async def asyncTearDown(self):
        from agents.provider import close_client
        await close_client()

    def test_resolve_config_featherless(self):
        from agents.provider import resolve_config
        os.environ.pop("FEATHERLESS_API_KEY", None)
        os.environ.pop("FEATHERLESS_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        os.environ["FEATHERLESS_API_KEY"] = "key-f"
        os.environ["FEATHERLESS_MODEL"] = "my-model-f"
        config = resolve_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.provider_name, "featherless")
        self.assertEqual(config.api_key, "key-f")
        self.assertEqual(config.model, "my-model-f")
        self.assertIn("api.featherless.ai", config.base_url)

    def test_resolve_config_openai(self):
        from agents.provider import resolve_config
        os.environ.pop("FEATHERLESS_API_KEY", None)
        os.environ.pop("FEATHERLESS_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        os.environ["OPENAI_API_KEY"] = "key-o"
        os.environ["OPENAI_MODEL"] = "my-model-o"
        config = resolve_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.provider_name, "openai")
        self.assertEqual(config.api_key, "key-o")
        self.assertEqual(config.model, "my-model-o")
        self.assertIn("api.openai.com", config.base_url)

    def test_resolve_config_none(self):
        from agents.provider import resolve_config
        os.environ.pop("FEATHERLESS_API_KEY", None)
        os.environ.pop("FEATHERLESS_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        config = resolve_config()
        self.assertIsNone(config)

    async def test_client_lifecycle(self):
        from agents.provider import _get_client, close_client
        client = _get_client()
        self.assertFalse(client.is_closed)
        await close_client()
        self.assertTrue(client.is_closed)

    @unittest.mock.patch("agents.provider.resolve_config")
    async def test_chat_completion_no_provider(self, mock_resolve):
        mock_resolve.return_value = None
        from agents.provider import provider
        res = await provider.chat_completion("sys", "user")
        self.assertIsNone(res)

    @unittest.mock.patch("agents.provider._get_client")
    async def test_chat_completion_success(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client
        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello world"}}]
        }
        mock_client.post.return_value = mock_response

        from agents.provider import Provider, ProviderConfig
        config = ProviderConfig(api_key="key", base_url="http://mock", model="model")
        p = Provider(max_retries=1)
        res = await p.chat_completion("sys", "user", config=config)
        self.assertEqual(res, "Hello world")

    @unittest.mock.patch("agents.provider._get_client")
    async def test_chat_completion_success_json(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client
        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"winner_id": 2}'}}]
        }
        mock_client.post.return_value = mock_response

        from agents.provider import Provider, ProviderConfig
        config = ProviderConfig(api_key="key", base_url="http://mock", model="model")
        p = Provider(max_retries=1)
        res = await p.chat_completion("sys", "user", config=config, parse_json=True)
        self.assertEqual(res, {"winner_id": 2})

    @unittest.mock.patch("asyncio.sleep")
    @unittest.mock.patch("agents.provider._get_client")
    async def test_chat_completion_failures_and_retries(self, mock_get_client, mock_sleep):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.post.side_effect = Exception("connection error")

        from agents.provider import Provider, ProviderConfig
        config = ProviderConfig(api_key="key", base_url="http://mock", model="model")
        p = Provider(max_retries=3, retry_delay=0.1)
        res = await p.chat_completion("sys", "user", config=config)
        self.assertIsNone(res)
        self.assertEqual(mock_client.post.call_count, 3)
        mock_sleep.assert_called()

    @unittest.mock.patch("agents.provider._get_client")
    async def test_chat_completion_empty_choices(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client
        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_client.post.return_value = mock_response

        from agents.provider import Provider, ProviderConfig
        config = ProviderConfig(api_key="key", base_url="http://mock", model="model")
        p = Provider(max_retries=1)
        res = await p.chat_completion("sys", "user", config=config)
        self.assertIsNone(res)

    def test_provider_resolve(self):
        from agents.provider import Provider
        os.environ["FEATHERLESS_API_KEY"] = "test-key"
        p = Provider()
        config = p.resolve()
        self.assertIsNotNone(config)
        self.assertEqual(config.api_key, "test-key")

    @unittest.mock.patch("agents.provider._get_client")
    async def test_chat_completion_unparseable_json(self, mock_get_client):
        mock_client = unittest.mock.AsyncMock()
        mock_get_client.return_value = mock_client
        mock_response = unittest.mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "unparseable json content"}}]
        }
        mock_client.post.return_value = mock_response

        from agents.provider import Provider, ProviderConfig
        config = ProviderConfig(api_key="key", base_url="http://mock", model="model")
        p = Provider(max_retries=1)
        res = await p.chat_completion("sys", "user", config=config, parse_json=True)
        self.assertIsNone(res)


class TestDashboardAnalytics(unittest.TestCase):
    """Analytics should correlate persistent Band messages with Reconciler decisions."""

    def test_conflicts_are_counted_at_detection_and_resolution_message_positions(self):
        import dashboard.server as dashboard_server

        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp)
            bridge_db = data_path / "bridge.db"
            reconciler_db = data_path / "reconciler.db"

            conn = sqlite3.connect(bridge_db)
            conn.execute("""
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sender_name TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.executemany(
                "INSERT INTO events VALUES (?, 'message', ?, ?, ?)",
                [
                    (1, "first", "User", "1970-01-01T00:00:50+00:00"),
                    (2, "second", "Keeper", "1970-01-01T00:02:30+00:00"),
                    (3, "third", "Reconciler", "1970-01-01T00:04:10+00:00"),
                ],
            )
            conn.commit()
            conn.close()

            conn = sqlite3.connect(reconciler_db)
            conn.execute("""
                CREATE TABLE conflicts (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    resolution_fact_id INTEGER,
                    resolution_reason TEXT,
                    severity TEXT,
                    score_confidence REAL
                )
            """)
            conn.executemany(
                "INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("resolved-1", "runtime", "python", "resolved", 200, 300,
                     2, "code wins", "high", 0.9),
                    ("open-1", "auth", "provider", "open", 400, None,
                     None, None, "medium", 0.7),
                ],
            )
            conn.commit()
            conn.close()

            old_data_path = dashboard_server.DATA_PATH
            dashboard_server.DATA_PATH = data_path
            try:
                analytics = dashboard_server._get_analytics_data()
            finally:
                dashboard_server.DATA_PATH = old_data_path

            self.assertEqual(analytics["summary"]["messages"], 3)
            self.assertEqual(analytics["summary"]["detected"], 2)
            self.assertEqual(analytics["summary"]["resolved"], 1)
            self.assertEqual(analytics["summary"]["open"], 1)
            self.assertEqual(analytics["summary"]["resolution_rate"], 50.0)
            self.assertEqual(analytics["conflicts"][0]["detected_after_messages"], 2)
            self.assertEqual(analytics["conflicts"][0]["resolved_after_messages"], 3)
            self.assertEqual(analytics["conflicts"][1]["detected_after_messages"], 3)


if __name__ == "__main__":
    unittest.main()
