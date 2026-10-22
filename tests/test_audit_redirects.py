"""Run the actual urllib redirect chain with synthetic HTTP connections only."""

from email.message import Message
import http.client
import io
import json
import unittest
from unittest.mock import patch
import urllib.request

import ai_client
import rewards
import testing
import twitch_auth

VALID_IDENTITY = b'{"client_id":"client","login":"helper","scopes":["chat:read","chat:edit"]}'

class Response(io.BytesIO):
    def __init__(self, status, body=b"", location=""):
        super().__init__(body)
        self.status = self.code = status
        self.reason = "Found" if status == 302 else "OK"
        self.headers = Message()
        if location:
            self.headers["Location"] = location

    def info(self):
        return self.headers


class AuthorizationRedirectTests(unittest.TestCase):
    def chain(self, invoke, *, source, destination, body):
        calls = []

        class Connection(http.client.HTTPConnection):
            sock = None
            debuglevel = 0

            def __init__(self, host, **options):
                self.host = host

            def set_debuglevel(self, value):
                pass

            def request(self, method, path, data=None, headers=None, **options):
                calls.append((self.host, method, {key.lower(): value for key, value in headers.items()}))

            def getresponse(self):
                return Response(302, location=destination) if len(calls) == 1 else Response(200, body)

            def close(self):
                pass

        with patch("http.client.HTTPSConnection", Connection), patch("http.client.HTTPConnection", Connection), \
                patch.object(urllib.request.ProxyHandler, "proxy_open", return_value=None):
            result = invoke()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], source)
        self.assertIn("authorization", calls[0][2])
        return result, calls

    def test_ai_key_is_removed_on_cross_origin_redirect(self):
        cfg = dict(AI_CHAT_URL="https://provider.invalid/chat/completions", AI_API_KEY="synthetic-key")
        result, calls = self.chain(lambda: ai_client.request_completion(cfg, "chosen", [{"role": "user", "content": "Привет"}]),
            source="provider.invalid", destination="https://outside.invalid/target",
            body=json.dumps({"choices": [{"message": {"content": "Ответ"}}]}).encode())
        self.assertEqual(result, "Ответ")
        self.assertNotIn("authorization", calls[1][2])

    def test_catalog_key_is_removed_on_cross_origin_redirect(self):
        auth = testing.credentials("https://provider.invalid/v1", "synthetic-key")
        result, calls = self.chain(lambda: testing.fetch_models(auth), source="provider.invalid",
            destination="https://outside.invalid/models", body=b'{"data":[{"id":"chosen"}]}')
        self.assertEqual(result[0].id, "chosen")
        self.assertNotIn("authorization", calls[1][2])

    def test_twitch_validation_and_helix_tokens_are_removed_on_cross_origin_redirect(self):
        for name, invoke, source in (
                ("validate", lambda: twitch_auth.validate("synthetic-token"), "id.twitch.tv"),
                ("helix", lambda: rewards.api_json("/users", "client", "synthetic-token"), "api.twitch.tv")):
            with self.subTest(name=name):
                result, calls = self.chain(invoke, source=source, destination="https://outside.invalid/target",
                                          body=VALID_IDENTITY if name == 'validate' else b'{}')
                self.assertIsInstance(result, dict)
                self.assertNotIn("authorization", calls[1][2])

    def test_same_origin_redirect_retains_authentication(self):
        result, calls = self.chain(lambda: twitch_auth.validate("synthetic-token"), source="id.twitch.tv",
            destination="https://id.twitch.tv:443/canonical", body=VALID_IDENTITY)
        self.assertEqual(result['login'], 'helper')
        self.assertEqual(calls[1][2]["authorization"], calls[0][2]["authorization"])

    def test_downgrade_or_port_change_removes_authentication(self):
        for destination in ("http://id.twitch.tv/target", "https://id.twitch.tv:444/target"):
            with self.subTest(destination=destination):
                _, calls = self.chain(lambda: twitch_auth.validate("synthetic-token"), source="id.twitch.tv",
                    destination=destination, body=VALID_IDENTITY)
                self.assertNotIn("authorization", calls[1][2])


if __name__ == "__main__":
    unittest.main()
