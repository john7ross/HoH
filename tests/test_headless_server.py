import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from llm_harness.a2a import A2AClient, A2AConnection
from llm_harness.a2a_push import shared_push_inbox
from llm_harness.headless_server import HeadlessServer, HeadlessServerError, _BoundHTTPServer


TOKEN = "headless-test-token-at-least-24-chars"
PUSH_TOKEN = "headless-push-token-at-least-24-chars"


class HeadlessServerTests(unittest.TestCase):
    def _request(self, url: str, path: str, *, token: str | None = TOKEN, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_the_server_does_not_bind_a_port_in_use_on_windows(self):
        """SO_REUSEADDR means different things on the two platforms.

        On POSIX it waives TIME_WAIT and a server needs it. On Windows it allows
        a bind onto a port another socket is actively listening on, after which
        the two split incoming connections -- seen as a request to a freshly
        started server coming back as a closed connection.
        """
        self.assertEqual(_BoundHTTPServer.allow_reuse_address, os.name != "nt")

    def test_health_auth_and_project_registration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            server = HeadlessServer(
                host="127.0.0.1",
                port=0,
                registry_path=root / "workspace.json",
                environment={"HOH_SERVER_TOKEN": TOKEN},
            )
            with server:
                status, health = self._request(server.url, "/v1/health", token=None)
                self.assertEqual(200, status)
                self.assertTrue(health["ok"])
                status, openapi = self._request(server.url, "/v1/openapi.json", token=None)
                self.assertEqual("3.1.0", openapi["openapi"])
                with self.assertRaises(HTTPError) as denied:
                    self._request(server.url, "/v1/projects", token="wrong-token-with-enough-length")
                self.assertEqual(401, denied.exception.code)

                status, created = self._request(
                    server.url, "/v1/projects", body={"root": str(project), "name": "Example"}
                )
                self.assertEqual(201, status)
                project_id = created["project"]["project_id"]
                status, listed = self._request(server.url, "/v1/projects")
                self.assertEqual(project_id, listed["projects"][0]["project_id"])
                status, queue = self._request(server.url, f"/v1/projects/{project_id}/queue")
                self.assertEqual([], queue["tasks"])

    def test_authenticated_a2a_push_route_delivers_to_runtime_inbox(self):
        task_id = "headless-push-task"
        server = HeadlessServer(
            host="127.0.0.1",
            port=0,
            environment={"HOH_SERVER_TOKEN": TOKEN, "HOH_A2A_PUSH_TOKEN": PUSH_TOKEN},
        )
        event = {"statusUpdate": {"taskId": task_id, "status": {"state": "TASK_STATE_COMPLETED"}}}
        with server:
            with self.assertRaises(HTTPError) as denied:
                self._request(server.url, "/v1/a2a/push", token=TOKEN, body=event)
            self.assertEqual(401, denied.exception.code)
            status, accepted = self._request(
                server.url, "/v1/a2a/push", token=PUSH_TOKEN, body=event
            )
            self.assertEqual(202, status)
            self.assertEqual(task_id, accepted["task_id"])
            events = shared_push_inbox().wait(task_id, timeout_seconds=1)
        self.assertEqual(1, len(events))

    def test_headless_push_route_completes_default_a2a_client(self):
        task_id = "headless-runtime-task"
        server = HeadlessServer(
            host="127.0.0.1",
            port=0,
            environment={"HOH_SERVER_TOKEN": TOKEN, "HOH_A2A_PUSH_TOKEN": PUSH_TOKEN},
        )

        def transport(url, method, headers, body, timeout, limit):
            del headers, timeout, limit
            if method == "GET":
                card = {
                    "name": "Remote",
                    "version": "1.0",
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example/a2a/v1",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                    "capabilities": {"streaming": False},
                    "skills": [],
                }
                return 200, {}, json.dumps(card).encode(), url
            request = json.loads(body)
            configured = request["params"]["configuration"]["taskPushNotificationConfig"]
            self.assertEqual("https://hoh.example/v1/a2a/push", configured["url"])
            event = {
                "statusUpdate": {
                    "taskId": task_id,
                    "status": {
                        "state": "TASK_STATE_COMPLETED",
                        "message": {"parts": [{"text": "headless-push-complete"}]},
                    },
                }
            }
            status, _accepted = self._request(
                server.url, "/v1/a2a/push", token=PUSH_TOKEN, body=event
            )
            self.assertEqual(202, status)
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"task": {"id": task_id, "status": {"state": "TASK_STATE_WORKING"}}},
            }
            return 200, {}, json.dumps(response).encode(), url

        with server, patch.dict(os.environ, {"PUSH_ENV": PUSH_TOKEN}, clear=False):
            result = A2AClient(
                A2AConnection(
                    "https://agent.example",
                    push_callback_url="https://hoh.example/v1/a2a/push",
                    push_token_env="PUSH_ENV",
                    timeout_seconds=2,
                ),
                transport=transport,
            ).send_message("hello")
        self.assertEqual("headless-push-complete", result.text)

    def test_requires_strong_token_and_tls_off_loopback(self):
        with self.assertRaisesRegex(HeadlessServerError, "at least 24"):
            HeadlessServer(environment={"HOH_SERVER_TOKEN": "short"})
        with self.assertRaisesRegex(HeadlessServerError, "HOH_A2A_PUSH_TOKEN"):
            HeadlessServer(
                environment={"HOH_SERVER_TOKEN": TOKEN, "HOH_A2A_PUSH_TOKEN": "short"}
            )
        with self.assertRaisesRegex(HeadlessServerError, "requires --tls-cert"):
            HeadlessServer(host="0.0.0.0", environment={"HOH_SERVER_TOKEN": TOKEN})


if __name__ == "__main__":
    unittest.main()
