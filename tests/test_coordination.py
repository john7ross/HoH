from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from llm_harness.coordination import (
    CoordinationConfig,
    LockContendedError,
    state_lease,
)
from llm_harness.cli import main
from llm_harness.domain import WorkItem
from llm_harness.state import HohStateStore, StateStoreError


class CoordinationTests(unittest.TestCase):
    def test_same_process_thread_contention_obeys_bounded_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            ready = threading.Event()
            release = threading.Event()
            holder_lease = state_lease(root)

            def hold() -> None:
                with holder_lease.hold("thread.hold"):
                    ready.set()
                    release.wait(timeout=5)

            thread = threading.Thread(target=hold)
            thread.start()
            self.assertTrue(ready.wait(timeout=2))
            config = CoordinationConfig(
                state_lock_timeout_seconds=0.1,
                poll_interval_seconds=0.01,
            )
            started = time.monotonic()
            try:
                with self.assertRaises(LockContendedError):
                    state_lease(root, config).acquire("thread.contender")
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                release.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_lock_status_cli_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "lock-status",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(root / "state"),
                        "--json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["message_type"], "coordination.lock_status")
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["data"]["locks"]), 2)
            self.assertTrue(all(item["available"] for item in payload["data"]["locks"]))

    def test_live_owner_cannot_be_broken_and_reports_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            ready = Path(tmp) / "ready"
            child = self._start_holder(root, ready, crash=False)
            try:
                self._wait_for(ready)
                config = CoordinationConfig(
                    state_lock_timeout_seconds=0.1,
                    poll_interval_seconds=0.01,
                )
                lease = state_lease(root, config)
                status = lease.status()
                self.assertFalse(status.available)
                self.assertFalse(status.stale_owner)
                self.assertEqual(status.owner["action"], "test.hold")
                self.assertEqual(status.owner["pid"], child.pid)
                with self.assertRaises(LockContendedError) as caught:
                    lease.recover(command="test recover")
                self.assertEqual(caught.exception.owner["pid"], child.pid)
            finally:
                child.terminate()
                child.communicate(timeout=5)

    def test_crashed_owner_is_recovered_without_deleting_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            ready = Path(tmp) / "ready"
            child = self._start_holder(root, ready, crash=True)
            self._wait_for(ready)
            child.communicate(timeout=5)

            lease = state_lease(root)
            before = lease.status()
            self.assertTrue(before.available)
            self.assertTrue(before.stale_owner)
            owner = lease.recover(command="test recover")
            self.assertEqual(owner["recovered_from"]["pid"], child.pid)
            self.assertTrue(lease.lock_path.exists())
            after = lease.status()
            self.assertTrue(after.available)
            self.assertFalse(after.stale_owner)

    def test_two_processes_cannot_claim_the_same_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            trigger = Path(tmp) / "go"
            store = HohStateStore(root)
            store.enqueue(
                WorkItem(
                    id="claim-once",
                    title="Claim once",
                    objective="Only one process may claim this task.",
                    acceptance_criteria=("One claimant.",),
                    verification_commands=(),
                )
            )
            code = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from llm_harness.state import HohStateStore\n"
                "root,go=Path(sys.argv[1]),Path(sys.argv[2])\n"
                "while not go.exists(): time.sleep(0.005)\n"
                "task=HohStateStore(root).claim_next()\n"
                "print(task.work_item.id if task else 'none', flush=True)\n"
            )
            first = self._popen(code, root, trigger)
            second = self._popen(code, root, trigger)
            trigger.write_text("go", encoding="utf-8")
            outputs = {
                first.communicate(timeout=10)[0].strip(),
                second.communicate(timeout=10)[0].strip(),
            }
            self.assertEqual(outputs, {"claim-once", "none"})
            task = store.latest_task("claim-once")
            self.assertEqual(task.status, "running")
            self.assertEqual(task.attempts, 1)

    def test_concurrent_evidence_appends_preserve_every_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            trigger = Path(tmp) / "go"
            code = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from llm_harness.state import HohStateStore\n"
                "root,go,prefix=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]\n"
                "while not go.exists(): time.sleep(0.005)\n"
                "store=HohStateStore(root)\n"
                "for i in range(10): store.record_operator_event('test',True,f'{prefix}-{i}',f'{prefix}-{i}')\n"
            )
            first = self._popen(code, root, trigger, "a")
            second = self._popen(code, root, trigger, "b")
            trigger.write_text("go", encoding="utf-8")
            self.assertEqual(first.communicate(timeout=15)[0], "")
            self.assertEqual(second.communicate(timeout=15)[0], "")
            store = HohStateStore(root)
            self.assertEqual(len(store.operator_events()), 20)
            self.assertEqual(len(store.journal.events()), 20)

    def test_corrupted_state_fails_closed_and_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            root.mkdir()
            queue = root / "queue.json"
            queue.write_text('{"partial":', encoding="utf-8")
            original = queue.read_bytes()
            store = HohStateStore(root)
            with self.assertRaisesRegex(StateStoreError, "Corrupted or unreadable JSON"):
                store.enqueue(
                    WorkItem(
                        id="blocked",
                        title="Blocked",
                        objective="Must not overwrite corrupt state.",
                        acceptance_criteria=("Fail closed.",),
                        verification_commands=(),
                    )
                )
            self.assertEqual(queue.read_bytes(), original)

    def _start_holder(self, root: Path, ready: Path, *, crash: bool) -> subprocess.Popen[str]:
        ending = "os._exit(0)" if crash else "time.sleep(30)"
        code = (
            "import os,sys,time\n"
            "from pathlib import Path\n"
            "from llm_harness.coordination import state_lease\n"
            "root,ready=Path(sys.argv[1]),Path(sys.argv[2])\n"
            "lease=state_lease(root)\n"
            "lease.acquire('test.hold', command='coordination test holder')\n"
            "ready.write_text('ready', encoding='utf-8')\n"
            f"{ending}\n"
        )
        return self._popen(code, root, ready)

    def _popen(self, code: str, *args: object) -> subprocess.Popen[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        return subprocess.Popen(
            [sys.executable, "-c", code, *(str(arg) for arg in args)],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _wait_for(self, path: Path) -> None:
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail(f"Timed out waiting for {path}")
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
