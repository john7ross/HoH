import unittest

from llm_harness.agent_matrix import (
    agent_matrix,
    current_platform,
    infer_declared_version,
)


CATALOG = {
    "agents": [
        {
            "name": "codex",
            "display_name": "Codex",
            "available": True,
            "version": "1.7.0",
            "detail": "npx available",
            "supervisor_driver": "acp",
            "worker_driver": "acp",
            "critic_driver": "acp",
        },
        {
            "name": "aider",
            "display_name": "aider",
            "available": False,
            "detail": "command is not on PATH",
            "worker_driver": "process",
        },
    ]
}


class AgentMatrixTests(unittest.TestCase):
    def test_one_row_per_agent_and_role_its_driver_covers(self):
        matrix = agent_matrix(CATALOG)

        rows = {(item["agent"], item["role"]): item for item in matrix["entries"]}
        self.assertEqual(
            set(rows),
            {("codex", "supervisor"), ("codex", "worker"), ("codex", "critic"), ("aider", "worker")},
        )
        self.assertEqual(rows[("codex", "worker")]["status"], "available")
        self.assertEqual(rows[("codex", "worker")]["version"], "1.7.0")
        self.assertEqual(rows[("aider", "worker")]["status"], "unavailable")
        self.assertEqual(rows[("aider", "worker")]["version"], "unknown")

    def test_vocabulary_says_only_what_can_be_observed(self):
        matrix = agent_matrix(CATALOG)

        self.assertEqual(matrix["statuses"], ["available", "unavailable"])
        self.assertEqual(matrix["available_count"], 3)
        self.assertTrue(matrix["platform"])
        self.assertNotIn("certifications", matrix)
        self.assertNotIn("controller_fingerprint", matrix)

    def test_empty_catalog_is_not_an_error(self):
        self.assertEqual(agent_matrix({})["entries"], [])

    def test_pinned_launch_argument_declares_the_version(self):
        self.assertEqual(
            infer_declared_version(("-y", "@agentclientprotocol/codex-acp@1.7.0")),
            "1.7.0",
        )
        self.assertIsNone(infer_declared_version(("-y", "@scope/package")))

    def test_platform_string_identifies_the_machine_for_a_bug_report(self):
        platform = current_platform()

        self.assertIn("python-", platform)
        self.assertEqual(platform, platform.casefold())


if __name__ == "__main__":
    unittest.main()
