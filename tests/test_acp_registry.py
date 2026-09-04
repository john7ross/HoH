import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.acp_registry import (
    AcpRegistryAgent,
    load_acp_registry,
    resolve_acp_launch,
)
from llm_harness.config import HarnessConfig
from llm_harness.role_profiles import ProjectRoleProfile, RoleSelection, apply_role_profile


class AcpRegistryTests(unittest.TestCase):
    def test_cache_parser_preserves_pinned_registry_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "registry.json"
            cache.write_text(
                json.dumps(
                    {
                        "version": "1.0.0",
                        "agents": [
                            {
                                "id": "future-acp",
                                "name": "Future",
                                "version": "2.3.4",
                                "description": "Future agent",
                                "distribution": {
                                    "npx": {
                                        "package": "future-acp@2.3.4",
                                        "args": ["--acp"],
                                        "env": {"NO_UPDATE": "1"},
                                    }
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            registry = load_acp_registry(cache)

        self.assertEqual(registry.version, "1.0.0")  # type: ignore[union-attr]
        self.assertEqual(registry.agents[0].version, "2.3.4")  # type: ignore[union-attr]

    @patch("llm_harness.acp_registry.shutil.which", return_value="C:/node/npx.CMD")
    def test_npx_launch_is_pinned_and_carries_registry_environment(self, _which):
        agent = AcpRegistryAgent(
            id="future-acp",
            name="Future",
            version="2.3.4",
            description="Future agent",
            distribution={
                "npx": {
                    "package": "future-acp@2.3.4",
                    "args": ["--acp"],
                    "env": {"NO_UPDATE": "1"},
                }
            },
        )

        launch = resolve_acp_launch(agent)

        self.assertTrue(launch.available)
        self.assertEqual(launch.args, ("-y", "future-acp@2.3.4", "--acp"))
        self.assertEqual(dict(launch.environment), {"NO_UPDATE": "1"})

    @patch("llm_harness.acp_registry.shutil.which", return_value="C:/node/npx.CMD")
    def test_registry_agent_can_fill_all_three_roles(self, _which):
        registry = type("Registry", (), {})()
        registry.agents = (
            AcpRegistryAgent(
                id="future-acp",
                name="Future",
                version="2.3.4",
                description="Future agent",
                distribution={"npx": {"package": "future-acp@2.3.4"}},
            ),
        )
        profile = ProjectRoleProfile(
            supervisor=RoleSelection("future-acp"),
            worker=RoleSelection("future-acp"),
            critic=RoleSelection("future-acp"),
        )

        config = apply_role_profile(HarnessConfig(), profile, registry=registry)

        self.assertEqual(config.supervisor.driver, "acp")
        self.assertEqual(config.worker.driver, "acp")
        self.assertEqual(config.critic.driver, "acp")
        self.assertEqual(config.worker.args, ("-y", "future-acp@2.3.4"))


if __name__ == "__main__":
    unittest.main()
