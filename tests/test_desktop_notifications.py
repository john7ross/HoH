from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.desktop_notifications import DesktopNotifier
from llm_harness.durable_io import read_jsonl


class DesktopNotificationTests(unittest.TestCase):
    def test_windows_notification_is_sent_without_shell_interpolation_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "notifications.jsonl"
            runner = unittest.mock.Mock(
                return_value=subprocess.CompletedProcess(args=(), returncode=0, stdout="", stderr="")
            )
            notifier = DesktopNotifier(log, runner=runner)
            with (
                patch("llm_harness.desktop_notifications.platform.system", return_value="Windows"),
                patch("llm_harness.desktop_notifications.shutil.which", return_value="powershell.exe"),
            ):
                record = notifier.notify("HoH", "Project <ready> & safe")

            self.assertTrue(record.delivered)
            command = runner.call_args.args[0]
            self.assertEqual(command[-1], "-")
            script = runner.call_args.kwargs["input"]
            self.assertIn("Project &lt;ready&gt; &amp; safe", script)
            self.assertEqual(len(read_jsonl(log)), 1)


if __name__ == "__main__":
    unittest.main()
