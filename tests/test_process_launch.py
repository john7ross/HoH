import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from llm_harness.process_launch import (
    process_group_kwargs,
    terminate_process_tree,
)


class _FakeProcess:
    pid = 4321

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        raise AssertionError("Windows must terminate the complete process tree")

    def kill(self):
        raise AssertionError("taskkill should complete before fallback")


class ProcessLaunchTests(unittest.TestCase):
    @patch("llm_harness.process_launch.os.name", "nt")
    @patch("llm_harness.process_launch.subprocess.run")
    def test_windows_terminates_cmd_shim_process_tree(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        terminate_process_tree(_FakeProcess())  # type: ignore[arg-type]

        self.assertEqual(run.call_args.args[0][:4], ("taskkill", "/PID", "4321", "/T"))

    def test_process_group_kwargs_match_the_platform(self):
        kwargs = process_group_kwargs()

        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
        else:
            self.assertEqual(kwargs, {"start_new_session": True})

    @unittest.skipIf(os.name == "nt", "POSIX process groups are not available on Windows.")
    def test_posix_termination_reaches_grandchildren(self):
        # The parent spawns a child and exits the wait; terminating only the parent
        # would leave the grandchild running and holding the attempt worktree.
        parent_source = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", parent_source],
            stdout=subprocess.PIPE,
            text=True,
            **process_group_kwargs(),
        )
        try:
            grandchild_pid = int(process.stdout.readline().strip())

            terminate_process_tree(process, timeout_seconds=5.0)

            self.assertIsNotNone(process.poll())
            self.assertFalse(_process_alive(grandchild_pid))
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def _process_alive(pid: int) -> bool:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        time.sleep(0.05)
    return True


if __name__ == "__main__":
    unittest.main()
