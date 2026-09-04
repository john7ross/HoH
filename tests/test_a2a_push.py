import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from llm_harness.a2a_push import A2APushInbox, A2APushReceiver


class A2APushTests(unittest.TestCase):
    def test_receiver_rejects_wrong_token_and_delivers_authenticated_event(self):
        inbox = A2APushInbox()
        receiver = A2APushReceiver(inbox, token_env="PUSH_TOKEN", environment={"PUSH_TOKEN": "secret"})
        with receiver:
            body = json.dumps(
                {"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_COMPLETED"}}}
            ).encode()
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(receiver.callback_url, data=body, method="POST"), timeout=2)
            self.assertEqual(rejected.exception.code, 401)
            request = Request(
                receiver.callback_url,
                data=body,
                headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 202)

            events = inbox.wait("task-1", timeout_seconds=1)

        self.assertEqual(len(events), 1)
        self.assertIn("statusUpdate", events[0])


if __name__ == "__main__":
    unittest.main()
