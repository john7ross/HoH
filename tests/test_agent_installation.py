import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from llm_harness.acp_registry import AcpRegistryAgent, resolve_acp_launch
from llm_harness.agent_installation import AgentInstallationError, ManagedAgentInstaller


def _agent(distribution):
    return AcpRegistryAgent(
        id="future-acp",
        name="Future",
        version="2.3.4",
        description="Future agent",
        distribution=distribution,
    )


class ManagedAgentInstallationTests(unittest.TestCase):
    def test_verified_zip_installs_atomically_and_is_preferred_by_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("nested/future-acp.exe", b"binary")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            agent = _agent(
                {
                    "binary": {
                        "windows-x86_64": {
                            "archive": "https://downloads.example/future.zip",
                            "cmd": "./future-acp.exe",
                            "args": ["--acp"],
                            "sha256": digest,
                        }
                    }
                }
            )

            def download(_url, destination, _limit, _timeout):
                shutil.copyfile(source, destination)
                return digest

            with patch("llm_harness.agent_installation.current_platform_key", return_value="windows-x86_64"):
                launch = ManagedAgentInstaller(root / "managed", downloader=download).install(agent)

            self.assertTrue(launch.available)
            self.assertEqual(launch.distribution, "managed-binary")
            self.assertEqual(launch.args, ("--acp",))
            self.assertTrue(Path(launch.command).is_file())
            receipt = json.loads((root / "managed" / "future-acp" / "2.3.4" / "receipt.json").read_text())
            self.assertEqual(receipt["integrity_sha256"], digest)

            with (
                patch.dict("os.environ", {"HOH_AGENT_HOME": str(root / "managed")}, clear=False),
                patch("llm_harness.agent_installation.current_platform_key", return_value="windows-x86_64"),
            ):
                preferred = resolve_acp_launch(agent)
            self.assertEqual(preferred.distribution, "managed-binary")

    def test_binary_without_sha256_is_blocked(self):
        agent = _agent(
            {
                "binary": {
                    "windows-x86_64": {
                        "archive": "https://downloads.example/future.zip",
                        "cmd": "future-acp.exe",
                    }
                }
            }
        )
        with tempfile.TemporaryDirectory() as tmp, patch(
            "llm_harness.agent_installation.current_platform_key", return_value="windows-x86_64"
        ):
            installer = ManagedAgentInstaller(Path(tmp))
            self.assertFalse(installer.status(agent).installable)
            with self.assertRaisesRegex(AgentInstallationError, "SHA-256"):
                installer.install(agent)

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../escape.exe", b"bad")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            agent = _agent(
                {
                    "binary": {
                        "windows-x86_64": {
                            "archive": "https://downloads.example/future.zip",
                            "cmd": "future-acp.exe",
                            "sha256": digest,
                        }
                    }
                }
            )

            def download(_url, destination, _limit, _timeout):
                shutil.copyfile(source, destination)
                return digest

            with patch("llm_harness.agent_installation.current_platform_key", return_value="windows-x86_64"):
                with self.assertRaisesRegex(AgentInstallationError, "Unsafe archive"):
                    ManagedAgentInstaller(root / "managed", downloader=download).install(agent)
            self.assertFalse((root / "escape.exe").exists())

    def test_pinned_npm_package_materializes_executable_and_can_be_uninstalled(self):
        agent = _agent(
            {
                "npx": {
                    "package": "@scope/future-acp@2.3.4",
                    "args": ["--acp"],
                    "env": {"NO_UPDATE": "1"},
                }
            }
        )

        def runner(args, _environment, _timeout):
            prefix = Path(args[args.index("--prefix") + 1])
            package = prefix / "node_modules" / "@scope" / "future-acp"
            package.mkdir(parents=True)
            (package / "cli.js").write_text("// test", encoding="utf-8")
            (package / "package.json").write_text(
                json.dumps({"bin": {"future-acp": "cli.js"}}), encoding="utf-8"
            )
            (prefix / "package-lock.json").write_text("{}", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "runtime" / "node.exe"
            node.parent.mkdir()
            node.write_bytes(b"node")
            with patch(
                "llm_harness.agent_installation.shutil.which",
                side_effect=lambda value: str(Path(tmp) / "runtime" / "npm.cmd") if value == "npm" else str(node),
            ):
                installer = ManagedAgentInstaller(Path(tmp) / "managed", process_runner=runner)
                launch = installer.install(agent)
                self.assertEqual(launch.command, str(node.resolve()))
                self.assertTrue(Path(launch.args[0]).is_file())
                self.assertEqual(launch.args[1:], ("--acp",))
                self.assertEqual(dict(launch.environment), {"NO_UPDATE": "1"})
                self.assertTrue(installer.uninstall(agent))
                self.assertIsNone(installer.resolve(agent))

    def test_npm_package_must_match_registry_version(self):
        agent = _agent({"npx": {"package": "future-acp@latest"}})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "llm_harness.agent_installation.shutil.which", return_value="C:/node/tool.exe"
        ):
            with self.assertRaisesRegex(AgentInstallationError, "pinned exactly"):
                ManagedAgentInstaller(Path(tmp)).install(agent)


if __name__ == "__main__":
    unittest.main()
