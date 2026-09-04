import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from llm_harness.oauth import (
    OAuthClient,
    OAuthClientConfig,
    OAuthError,
    clear_oauth_token_cache,
    require_secure_endpoint,
)
from llm_harness.oauth_store import OAuthTokenStore, StoredOAuthToken


class OAuthTests(unittest.TestCase):
    def tearDown(self):
        clear_oauth_token_cache()

    def test_client_credentials_uses_basic_auth_and_caches_token(self):
        calls = []

        def transport(url, method, headers, body, _timeout):
            calls.append((url, method, dict(headers), body))
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"access_token": "issued-token", "token_type": "Bearer", "expires_in": 600}
            ).encode()

        client = OAuthClient(
            OAuthClientConfig(
                auth_kind="oauth2",
                flow="client_credentials",
                client_id_env="CLIENT_ID",
                client_secret_env="CLIENT_SECRET",
                token_url="https://issuer.example/token",
                scopes=("a2a.execute",),
            ),
            environment={"CLIENT_ID": "client", "CLIENT_SECRET": "secret"},
            transport=transport,
        )

        self.assertEqual(client.access_token(), "issued-token")
        self.assertEqual(client.access_token(), "issued-token")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2]["Authorization"].startswith("Basic "))
        self.assertNotIn(b"secret", calls[0][3])

    def test_plaintext_token_endpoint_is_refused_before_the_secret_is_sent(self):
        calls = []

        def transport(url, method, headers, body, _timeout):
            calls.append(url)
            raise AssertionError("The client secret must never reach a plaintext endpoint.")

        client = OAuthClient(
            OAuthClientConfig(
                auth_kind="oauth2",
                flow="client_credentials",
                client_id_env="CLIENT_ID",
                client_secret_env="CLIENT_SECRET",
                token_url="http://issuer.example/token",
            ),
            environment={"CLIENT_ID": "client", "CLIENT_SECRET": "secret"},
            transport=transport,
        )

        with self.assertRaisesRegex(OAuthError, "requires HTTPS"):
            client.access_token()
        self.assertEqual(calls, [])

    def test_plaintext_discovery_endpoint_is_refused(self):
        def transport(url, method, headers, body, _timeout):
            raise AssertionError("Discovery must not be fetched over plaintext HTTP.")

        client = OAuthClient(
            OAuthClientConfig(
                auth_kind="oidc",
                flow="client_credentials",
                client_id_env="CLIENT_ID",
                client_secret_env="CLIENT_SECRET",
                discovery_url="http://issuer.example/.well-known/openid-configuration",
            ),
            environment={"CLIENT_ID": "client", "CLIENT_SECRET": "secret"},
            transport=transport,
        )

        with self.assertRaisesRegex(OAuthError, "requires HTTPS"):
            client.access_token()

    def test_loopback_endpoints_stay_usable_for_local_development(self):
        self.assertEqual(
            require_secure_endpoint("http://127.0.0.1:8080/token", "token endpoint"),
            "http://127.0.0.1:8080/token",
        )
        for rejected in (
            "https://user:pass@issuer.example/token",
            "ftp://issuer.example/token",
            "/relative/token",
        ):
            with self.assertRaises(OAuthError):
                require_secure_endpoint(rejected, "token endpoint")

    def test_user_token_store_round_trips_without_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OAuthTokenStore(Path(tmp))
            path = store.save(
                "issuer-client-scope",
                StoredOAuthToken("access-secret", "refresh-secret", time.time() + 600),
            )
            raw = path.read_text(encoding="utf-8")
            loaded = store.load("issuer-client-scope")

        self.assertNotIn("access-secret", raw)
        self.assertNotIn("refresh-secret", raw)
        self.assertEqual(loaded.access_token, "access-secret")
        self.assertEqual(loaded.refresh_token, "refresh-secret")

    def test_expired_device_token_is_refreshed_from_persistent_store(self):
        calls = []

        def transport(url, method, _headers, body, _timeout):
            calls.append((url, method, body))
            return 200, {}, json.dumps(
                {"access_token": "renewed", "token_type": "Bearer", "expires_in": 600}
            ).encode()

        config = OAuthClientConfig(
            auth_kind="oidc",
            flow="device_code",
            client_id_env="CLIENT_ID",
            token_url="https://issuer.example/token",
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = OAuthTokenStore(Path(tmp))
            seed = OAuthClient(config, environment={"CLIENT_ID": "public-client"}, token_store=store)
            store.save(
                seed._persistent_key(),
                StoredOAuthToken("expired", "refresh-secret", time.time() - 1),
            )
            client = OAuthClient(
                config,
                environment={"CLIENT_ID": "public-client"},
                transport=transport,
                token_store=store,
            )
            token = client.access_token()
            persisted = store.load(client._persistent_key())

        self.assertEqual(token, "renewed")
        self.assertEqual(persisted.refresh_token, "refresh-secret")
        self.assertIn(b"grant_type=refresh_token", calls[0][2])
        self.assertIn(b"refresh_token=refresh-secret", calls[0][2])

    @patch("llm_harness.oauth.time.sleep", return_value=None)
    def test_oidc_device_flow_discovers_endpoints_and_waits_for_user(self, _sleep):
        token_attempts = 0

        def transport(url, method, _headers, _body, _timeout):
            nonlocal token_attempts
            if method == "GET":
                payload = {
                    "token_endpoint": "https://issuer.example/token",
                    "device_authorization_endpoint": "https://issuer.example/device",
                }
            elif url.endswith("/device"):
                payload = {
                    "device_code": "device-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://issuer.example/activate",
                    "expires_in": 600,
                    "interval": 1,
                }
            else:
                token_attempts += 1
                payload = {"error": "authorization_pending"} if token_attempts == 1 else {
                    "access_token": "device-token", "token_type": "Bearer", "expires_in": 600
                }
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()

        shown = []
        client = OAuthClient(
            OAuthClientConfig(
                auth_kind="oidc",
                flow="device_code",
                client_id_env="CLIENT_ID",
                discovery_url="https://issuer.example/.well-known/openid-configuration",
                timeout_seconds=60,
            ),
            environment={"CLIENT_ID": "public-client"},
            transport=transport,
        )

        token = client.authorize_device(shown.append)

        self.assertEqual(token.access_token, "device-token")
        self.assertEqual(shown[0].user_code, "ABCD-EFGH")
        self.assertEqual(client.access_token(), "device-token")


if __name__ == "__main__":
    unittest.main()
