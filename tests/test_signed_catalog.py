import json
from pathlib import Path
import tempfile
import unittest

from llm_harness.signed_catalog import (
    SignedCatalogError,
    import_publisher,
    verify_signed_catalog,
)


MODULUS = "2LuhFjyiPT21EkR2EvU3m4GfAlGGVPmABj3ncgwVgParlPbhhHqliqqcrD_H6Qi3aIta62jwpGrxEIiIuwEqZQkcdA9NFgunlx61hoFKGWNACuAiAtOyYBSA7ufSkcHU7d5ra8XJ3I0MWouJHvNUUefCo6d2Lj1vjM3sImbBR7Lt4Rq6GB7fCfM6U1h-hE5RJxy8U1j6Sci-14F2TYWzr4TiOXrD65b3T7VcscD3OMEWtwxiv5uL7bCSSga1j76BaHCX0o--eaMDOIwWF-3qSzsAAtZpm_1G9OznuvpoAuj5waEgJhJHiSdz3336nKcpO6hezI0vEXu5I2smVNJ7gQ"
SIGNATURE = "EakqTIp3V-agyTDK9cxHNaBaiqaaVaS2t7UjWzK-DYPV3Z8sW6Mz9m_XER3m6xtvWJsJtONE-MIiVveyXU4ElqetDFmZ-ugMvtN0spBdESzpd4Fzg9jYSxnkcR8y8lYtqn5paUdl3MJadyu0NiJqlLc-r4lwBxO6trgVJdn1-_6Pe9AsG4nvP-Og86BSCIUR7Q37-EKUe3xPm-yfXIvkAMzdrHMomtsgvViI_mT3BaSYrNWpdZtMTLexkeDzOcmyrRyKMa2CSTgYuU37IYFvQWnDwz0fmIOv9uxIqSUVErOHHCHPZEVRku4rXQ8sVk9aiCg1BJK2NuD8Q1g5tS6fqw"


def publisher():
    return {"key_id": "test-2026", "name": "Test Publisher", "rsa": {"n": MODULUS, "e": "AQAB"}}


def envelope():
    return {
        "schema": "hoh.signed-catalog",
        "version": "1.0",
        "algorithm": "RS256",
        "key_id": "test-2026",
        "payload": {"artifacts": [], "generated_at_utc": "2026-08-29T00:00:00Z", "kind": "releases"},
        "signature": SIGNATURE,
    }


class SignedCatalogTests(unittest.TestCase):
    def test_verifies_trusted_rs256_catalog(self):
        payload = verify_signed_catalog(
            envelope(),
            {"schema": "hoh.trusted-publishers", "version": "1.0", "publishers": [publisher()]},
            expected_kind="releases",
        )
        self.assertEqual("releases", payload["kind"])

    def test_rejects_tampered_or_untrusted_catalog(self):
        changed = envelope()
        changed["payload"]["artifacts"] = [{"version": "9.9.9"}]
        with self.assertRaisesRegex(SignedCatalogError, "signature is invalid"):
            verify_signed_catalog(
                changed,
                {"schema": "hoh.trusted-publishers", "version": "1.0", "publishers": [publisher()]},
            )
        with self.assertRaisesRegex(SignedCatalogError, "not trusted"):
            verify_signed_catalog(
                envelope(),
                {"schema": "hoh.trusted-publishers", "version": "1.0", "publishers": []},
            )

    def test_imports_public_identity_without_private_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "publisher.json"
            source.write_text(json.dumps(publisher()), encoding="utf-8")
            trust = root / "trust.json"
            key_id = import_publisher(source, trust)
            saved = json.loads(trust.read_text(encoding="utf-8"))
        self.assertEqual("test-2026", key_id)
        self.assertNotIn("d", saved["publishers"][0]["rsa"])


if __name__ == "__main__":
    unittest.main()
