import json
import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock

from llm_harness.config import CloudModelEndpoint
from llm_harness.model_providers import model_endpoint_preflight
from llm_harness.secret_store import (
    SecretStore,
    SecretStoreError,
    resolve_credential,
    secret_store_path,
)


class SecretStoreTests(unittest.TestCase):
    def test_a_saved_credential_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp))

            store.set("OPENAI_API_KEY", "sk-proj-value")

            self.assertEqual(store.get("OPENAI_API_KEY"), "sk-proj-value")
            self.assertEqual(store.names(), ("OPENAI_API_KEY",))

    def test_the_value_is_not_readable_as_plain_text_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = SecretStore(root).set("DEEPSEEK_API_KEY", "sk-secret-value")

            raw = path.read_bytes()

            self.assertNotIn(b"sk-secret-value", raw)
            envelope = json.loads(raw.decode("utf-8"))
            expected = "windows-dpapi-user" if os.name == "nt" else "user-file-0600"
            self.assertEqual(envelope["protection"], expected)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not meaningful on Windows.")
    def test_the_file_is_readable_only_by_its_owner_on_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = SecretStore(Path(tmp)).set("OPENAI_API_KEY", "sk-value")

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_forgetting_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp))
            store.set("OPENAI_API_KEY", "sk-value")

            self.assertTrue(store.delete("OPENAI_API_KEY"))
            self.assertIsNone(store.get("OPENAI_API_KEY"))
            self.assertFalse(store.delete("OPENAI_API_KEY"))

    def test_only_environment_variable_names_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp))

            for rejected in ("", "  ", "1KEY", "my key", "../escape", "KEY-WITH-DASH"):
                with self.assertRaises(SecretStoreError):
                    store.set(rejected, "value")

    def test_an_empty_value_is_refused_rather_than_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SecretStoreError):
                SecretStore(Path(tmp)).set("OPENAI_API_KEY", "   ")

    def test_the_store_lives_in_the_user_profile_not_the_project(self):
        path = secret_store_path({"HOH_SECRET_STORE": ""} | dict(os.environ))

        self.assertNotIn("harness", path.parts[-2:])


class CredentialResolutionTests(unittest.TestCase):
    def test_the_environment_wins_over_a_saved_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp))
            store.set("OPENAI_API_KEY", "stored")

            resolved = resolve_credential("OPENAI_API_KEY", {"OPENAI_API_KEY": "exported"}, store)

            self.assertEqual(resolved, "exported")

    def test_the_store_answers_when_nothing_is_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp))
            store.set("OPENAI_API_KEY", "stored")

            self.assertEqual(resolve_credential("OPENAI_API_KEY", {}, store), "stored")

    def test_an_absent_credential_resolves_to_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_credential("OPENAI_API_KEY", {}, SecretStore(Path(tmp))))

    def test_preflight_accepts_a_saved_key_and_names_its_source(self):
        endpoint = CloudModelEndpoint(provider="openai", model="gpt-test", api_key_env="HOH_TEST_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(
                os.environ, {"HOH_SECRET_STORE": str(Path(tmp) / "secrets")}, clear=False
            ):
                blocked, blocked_detail = model_endpoint_preflight(endpoint, {})
                SecretStore().set("HOH_TEST_KEY", "sk-value")
                allowed, allowed_detail = model_endpoint_preflight(endpoint, {})

        self.assertFalse(blocked)
        self.assertIn("no credential", blocked_detail)
        self.assertTrue(allowed, allowed_detail)
        self.assertIn("credential_source=stored", allowed_detail)


if __name__ == "__main__":
    unittest.main()
