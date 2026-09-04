import unittest

from llm_harness.driver_registry import (
    DRIVER_PROTOCOL,
    DRIVER_PROTOCOL_VERSION,
    DriverRegistryError,
    driver_catalog,
    resolve_driver,
)


class DriverRegistryTests(unittest.TestCase):
    def test_catalog_is_versioned_and_role_scoped(self):
        workers = driver_catalog("worker")

        self.assertTrue(workers)
        self.assertTrue(all(item.protocol == DRIVER_PROTOCOL for item in workers))
        self.assertTrue(all(item.protocol_version == DRIVER_PROTOCOL_VERSION for item in workers))
        self.assertTrue(all(item.role == "worker" for item in workers))
        self.assertTrue(any(item.driver_id == "acp" for item in driver_catalog("supervisor")))
        self.assertTrue(any(item.driver_id == "model_json" for item in driver_catalog("critic")))

    def test_legacy_alias_resolves_to_canonical_manifest(self):
        self.assertEqual(resolve_driver("hermes", "worker").driver_id, "hermes_acp")
        self.assertEqual(resolve_driver("claude", "worker").driver_id, "claude_code")

    def test_unknown_driver_fails_closed(self):
        with self.assertRaisesRegex(DriverRegistryError, "Unsupported worker driver"):
            resolve_driver("made-up-runtime", "worker")


if __name__ == "__main__":
    unittest.main()
