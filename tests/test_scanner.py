import unittest
from unittest.mock import patch

from llm_harness.config import AgentConfig
from llm_harness.scanner import _probe_openai_compatible_model, scan_agents, scan_local_models
from llm_harness.targets import ExecutionTargetType, LocalModelEndpointTarget


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class ScannerTests(unittest.TestCase):
    def test_scan_agents_reports_available_and_missing_commands(self):
        agents = (
            AgentConfig(name="present", command="present-cli"),
            AgentConfig(name="missing", command="missing-cli"),
        )

        def resolver(command: str) -> str | None:
            return "C:/bin/present-cli.exe" if command == "present-cli" else None

        probes = scan_agents(agents, resolver=resolver)

        self.assertTrue(probes[0].available)
        self.assertEqual(probes[0].path, "C:/bin/present-cli.exe")
        self.assertFalse(probes[1].available)
        self.assertIsNone(probes[1].path)

    def test_scan_local_models_uses_endpoint_probe(self):
        models = (
            LocalModelEndpointTarget(
                name="qwen",
                base_url="http://127.0.0.1:8080",
                model="qwen",
            ),
        )

        probes = scan_local_models(models, endpoint_probe=lambda model: model.name == "qwen")

        self.assertEqual(probes[0].target_type, ExecutionTargetType.LOCAL_MODEL_ENDPOINT)
        self.assertTrue(probes[0].available)
        self.assertIn("http://127.0.0.1:8080", probes[0].detail)

    def test_openai_compatible_probe_requires_success_status(self):
        model = LocalModelEndpointTarget(
            name="qwen",
            base_url="http://127.0.0.1:8080",
            model="qwen",
        )

        with patch("llm_harness.scanner.urlopen", return_value=_Response(404)):
            self.assertFalse(_probe_openai_compatible_model(model))


if __name__ == "__main__":
    unittest.main()
