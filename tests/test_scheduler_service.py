from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from llm_harness.scheduler_service import manage_scheduler_service


class SchedulerServiceTests(unittest.TestCase):
    def test_packaged_application_can_supply_distribution_root_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "register-workspace-scheduler.sh"
            script.parent.mkdir()
            script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            runner = Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0, stdout="", stderr=""))
            with (
                patch.dict("os.environ", {"HOH_DISTRIBUTION_ROOT": str(root)}),
                patch("llm_harness.scheduler_service.platform.system", return_value="Linux"),
                patch("llm_harness.scheduler_service.shutil.which", return_value="/bin/bash"),
            ):
                result = manage_scheduler_service("status", runner=runner)

            self.assertTrue(result.ok)
            self.assertIn(str(script.resolve()), result.command)

    def test_windows_service_uses_bundled_registration_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "register-workspace-scheduler-task.ps1"
            script.parent.mkdir()
            script.write_text("param()", encoding="utf-8")
            runner = Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0, stdout="installed=true", stderr=""))
            with (
                patch("llm_harness.scheduler_service.platform.system", return_value="Windows"),
                patch("llm_harness.scheduler_service.shutil.which", return_value="powershell.exe"),
            ):
                result = manage_scheduler_service(
                    "install", every_minutes=2, run_now=True, distribution_root=root, runner=runner
                )

        self.assertTrue(result.ok)
        self.assertIn(str(script.resolve()), result.command)
        self.assertIn("-RunNow", result.command)
        self.assertEqual(result.command[result.command.index("-EveryMinutes") + 1], "2")

    def test_linux_and_macos_use_user_service_registration_script(self):
        for system in ("Linux", "Darwin"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                script = root / "scripts" / "register-workspace-scheduler.sh"
                script.parent.mkdir()
                script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
                runner = Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0, stdout="installed=true", stderr=""))
                with (
                    patch("llm_harness.scheduler_service.platform.system", return_value=system),
                    patch("llm_harness.scheduler_service.shutil.which", return_value="/bin/bash"),
                ):
                    result = manage_scheduler_service(
                        "install", every_minutes=5, run_now=True, distribution_root=root, runner=runner
                    )
                self.assertTrue(result.ok)
                self.assertEqual("/bin/bash", result.command[0])
                self.assertIn("--install", result.command)
                self.assertEqual(result.command[result.command.index("--every") + 1], "5")
                self.assertIn("--run-now", result.command)


if __name__ == "__main__":
    unittest.main()
