"""The real gateway must fail closed before inspecting requests or doing I/O."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from birdfusion.gateway import GatewayClient


class GatewayDisabledTests(unittest.TestCase):
    def test_all_route_model_and_cache_variants_fail_without_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            credential = root / "credentials.json"
            cache = root / "cache"
            cache.mkdir()
            (cache / "existing.json").write_text('{"choices": [1]}', encoding="utf-8")
            requests = [
                {},
                {"model": ""},
                {"model": None},
                {"model": "model-a", "messages": []},
                {"model": "model-b", "messages": []},
            ]
            configurations = [
                {},
                {"cache_dir": cache},
                {"routes": {"model-a": {"api_key": "dummy", "base_url": "http://example.invalid/v1"}},
                 "cache_dir": cache},
                {"routes": {"model-a": {"credential_config": str(credential)}}},
                {"channels": [{"name": "synthetic", "models": {"model-a": "mapped"},
                               "credential_config": str(credential)}], "cache_dir": cache},
            ]
            with patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")) as read_text, \
                 patch.object(Path, "exists", side_effect=AssertionError("unexpected cache lookup")) as exists, \
                 patch("urllib.request.build_opener", side_effect=AssertionError("unexpected opener")) as opener:
                for configuration in configurations:
                    client = GatewayClient(credential_config=credential, **configuration)
                    for request in requests:
                        with self.subTest(configuration=configuration, request=request):
                            with self.assertRaisesRegex(RuntimeError, "Gateway inference is disabled"):
                                client.complete(request)
                read_text.assert_not_called()
                exists.assert_not_called()
                opener.assert_not_called()

    def test_request_and_subclass_hook_are_not_inspected(self):
        class ExplodingRequest:
            def get(self, *_args):
                raise AssertionError("request inspected")

        class DerivedClient(GatewayClient):
            def _complete_unchecked_for_future_review(self, request):
                raise AssertionError("subclass hook invoked")

        with patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")) as read_text, \
             patch("urllib.request.build_opener", side_effect=AssertionError("unexpected opener")) as opener:
            with self.assertRaisesRegex(RuntimeError, "Gateway inference is disabled"):
                DerivedClient().complete(ExplodingRequest())
            read_text.assert_not_called()
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
