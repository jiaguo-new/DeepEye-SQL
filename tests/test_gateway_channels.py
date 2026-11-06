import json
import os
import urllib.error
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from birdfusion.gateway import GatewayClient


@unittest.skip("Historical transport removed: gateway is intentionally disabled; see test_gateway_disabled")
class GatewayChannelTests(unittest.TestCase):
    def test_deterministic_multi_channel_routing_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            servers = []
            counts = {"a": 0, "b": 0}
            bodies = []
            for name in ("a", "b"):
                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        length = int(self.headers.get("Content-Length", 0))
                        body = json.loads(self.rfile.read(length))
                        bodies.append((self.channel_name, body))
                        counts[self.channel_name] += 1
                        payload = json.dumps({"model": body["model"],
                                              "choices": [{"message": {"content": "SELECT 1"}}]}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    def log_message(self, *args):
                        pass
                Handler.channel_name = name
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                servers.append(server)
                (root / f"{name}.json").write_text(json.dumps({
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1", "key": "dummy"
                }), encoding="utf-8")
            channels = [
                {"name": "a", "models": {"deepseek-v4-pro": "deepseek-v4-pro"},
                 "credential_config": str(root / "a.json")},
                {"name": "b", "models": {"deepseek-v4-pro": "deepseek-v4-pro-aliyun"},
                 "credential_config": str(root / "b.json")},
            ]
            client = GatewayClient(channels=channels, cache_dir=root / "cache", retries=1)
            first = None
            for seed in range(32):
                request = {"model": "deepseek-v4-pro", "seed": seed,
                           "messages": [{"role": "user", "content": "SQL"}]}
                response = client.complete(request)
                if seed == 0:
                    first = response
            self.assertEqual(set(counts), {"a", "b"})
            self.assertGreater(counts["a"], 0)
            self.assertGreater(counts["b"], 0)
            self.assertTrue(all(body["model"] == ("deepseek-v4-pro" if name == "a" else "deepseek-v4-pro-aliyun")
                                for name, body in bodies))
            before = dict(counts)
            replay = client.complete({"model": "deepseek-v4-pro", "seed": 0,
                                      "messages": [{"role": "user", "content": "SQL"}]})
            self.assertTrue(replay["_replay"])
            self.assertEqual(replay["model"], first["model"])
            self.assertEqual(counts, before)
            for server in servers:
                server.shutdown()
                server.server_close()

    def test_environment_proxy_does_not_hijack_gateway_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            received = []
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    received.append(dict(self.headers))
                    payload = json.dumps({"model": "qwen3.8-27b", "choices": [{"message": {"content": "SELECT 1"}}]}).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(payload)))
                    self.end_headers(); self.wfile.write(payload)
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                (root / "g.json").write_text(json.dumps({
                    "url": f"http://127.0.0.1:{server.server_port}/v1", "key": "dummy"}), encoding="utf-8")
                with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                                             "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
                                             "NO_PROXY": "", "no_proxy": ""}):
                    client = GatewayClient(root / "g.json", cache_dir=root / "cache", retries=1)
                    result = client.complete({"model":"qwen3.8-27b","messages":[{"role":"user","content":"SQL"}]})
                self.assertIn("SELECT 1", result["choices"][0]["message"]["content"])
                self.assertTrue(received)
            finally:
                server.shutdown(); server.server_close()

    def test_uncertain_errors_never_resend_or_cache(self):
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.payload

        failures = [
            urllib.error.HTTPError("http://test", 429, "rate limited", {}, None),
            urllib.error.HTTPError("http://test", 503, "unavailable", {}, None),
            urllib.error.URLError("network secret detail"),
            TimeoutError("timeout secret detail"),
            Response(b"not json"),
            Response(b'{"model":"qwen3.8-27b","choices":[]}'),
            Response(b'{"model":"wrong","choices":[{}]}'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "g.json"
            config.write_text(json.dumps({"base_url": "http://example.invalid/v1", "key": "secret"}))
            client = GatewayClient(config, cache_dir=root / "cache", retries=9)
            for failure in failures:
                with self.subTest(failure=type(failure).__name__), patch("urllib.request.build_opener") as build:
                    if isinstance(failure, Exception):
                        build.return_value.open.side_effect = failure
                    else:
                        build.return_value.open.return_value = failure
                    with self.assertRaises(RuntimeError) as raised:
                        client.complete({"model": "qwen3.8-27b"})
                    self.assertNotIn("secret", str(raised.exception))
                    self.assertEqual(build.return_value.open.call_count, 1)
                    self.assertFalse(list(root.glob("cache/*.json")))

    def test_response_model_alias_requires_exact_endpoint_and_request(self):
        class Response:
            def __init__(self, model): self.model = model
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"model": self.model, "choices": [{}]}).encode()

        cases = [
            ("http://gateway.internal.example/v1", "dm-deepseek-v4.1-flash", "deepseek-v4.1-flash", True),
            ("http://gateway.internal.example/v1", "dm-deepseek-v4.1-flash", "dm-deepseek-v4.1-flash", True),
            ("http://other.invalid/v1", "dm-deepseek-v4.1-flash", "deepseek-v4.1-flash", False),
            ("https://gateway.internal.example/v1", "dm-deepseek-v4.1-flash", "deepseek-v4.1-flash", False),
            ("http://gateway.internal.example/v1", "another-model", "deepseek-v4.1-flash", False),
            ("http://gateway.internal.example/v1", "qwen3.8-27b", None, False),
            ("http://gateway.internal.example/v1", "qwen3.8-27b", "Qwen3.8-27B", False),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "g.json"
            for endpoint, requested, actual, allowed in cases:
                with self.subTest(endpoint=endpoint, requested=requested, actual=actual):
                    config.write_text(json.dumps({"base_url": endpoint, "key": "dummy"}))
                    with patch("urllib.request.build_opener") as build:
                        build.return_value.open.return_value = Response(actual)
                        client = GatewayClient(config)
                        if allowed:
                            self.assertEqual(client.complete({"model": requested})["model"], actual)
                        else:
                            with self.assertRaises(RuntimeError):
                                client.complete({"model": requested})
                        self.assertEqual(build.return_value.open.call_count, 1)

    def test_cache_isolated_by_effective_route_and_credentials_and_validated(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"model":"qwen3.8-27b","choices":[{}]}'

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "g.json"
            config.write_text(json.dumps({"base_url": "http://one.invalid/v1", "key": "first-secret"}))
            request = {"model": "qwen3.8-27b"}
            cache = root / "cache"
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.return_value = Response()
                client = GatewayClient(config, cache_dir=cache)
                original = client.complete(request)
                self.assertTrue(client.complete(request)["_replay"])
                config.write_text(json.dumps({"base_url": "http://two.invalid/v1", "key": "first-secret"}))
                changed_endpoint = client.complete(request)
                config.write_text(json.dumps({"base_url": "http://two.invalid/v1", "key": "second-secret"}))
                changed_key = client.complete(request)
                host_client = GatewayClient(config, cache_dir=cache, routes={"qwen3.8-27b": {"host_header": "another-host"}})
                changed_host = host_client.complete(request)
                self.assertEqual(build.return_value.open.call_count, 4)
                self.assertEqual(len({r["_request_sha256"] for r in (original, changed_endpoint, changed_key, changed_host)}), 4)
                self.assertNotIn("secret", " ".join(p.name for p in cache.iterdir()))
                bad_cache = cache / (changed_key["_request_sha256"] + ".json")
                bad_cache.write_text(json.dumps({"model": "wrong", "choices": [{}]}))
                with self.assertRaises(ValueError): client.complete(request)
                self.assertEqual(build.return_value.open.call_count, 4)

    def test_direct_channel_and_route_specific_alias(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"model":"deepseek-v4.1-flash","choices":[{}]}'

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channels = [{"name": "direct", "models": {"alias": "dm-deepseek-v4.1-flash"},
                         "base_url": "http://gateway.internal.example/v1", "api_key": "secret"}]
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.return_value = Response()
                client = GatewayClient(root / "missing.json", cache_dir=root / "cache", channels=channels)
                self.assertEqual(client.complete({"model": "alias"})["model"], "deepseek-v4.1-flash")
                self.assertTrue(client.complete({"model": "alias"})["_replay"])
                self.assertEqual(build.return_value.open.call_count, 1)
                channels[0]["base_url"] = "http://127.0.0.1:18081/v1"
                channels[0]["host_header"] = "gateway.internal.example"
                with self.assertRaises(RuntimeError): client.complete({"model": "alias"})
                self.assertEqual(build.return_value.open.call_count, 2)

    def test_redirect_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = []
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    requests.append(self.path)
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.send_response(307)
                    self.send_header("Location", "/other")
                    self.end_headers()
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                config = root / "g.json"
                config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}/v1", "key": "dummy"}))
                with self.assertRaisesRegex(RuntimeError, "HTTP 307"):
                    GatewayClient(config, retries=5).complete({"model": "qwen3.8-27b"})
                self.assertEqual(requests, ["/v1/chat/completions"])
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
