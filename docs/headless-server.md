# HoH headless server

[Русский](headless-server.ru.md) · **English**

The headless server exposes the same workspace registry, Supervisor conversation, planning,
queue-loop, and metrics services used by the desktop UI. It does not create a second orchestration
path and does not bypass project/state/Git leases.

## Start on loopback

Create a random token outside project files and start the service:

```bash
export HOH_SERVER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export HOH_A2A_PUSH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bash scripts/hoh.sh server --host 127.0.0.1 --port 8765
```

On Windows use `scripts\hoh.ps1` and set the same environment variable before launch. The server
rejects tokens shorter than 24 characters and never prints their value. `/v1/health` and
`/v1/openapi.json` are public; every project or operation endpoint requires
`Authorization: Bearer <token>`.

`HOH_A2A_PUSH_TOKEN` is optional unless a remote A2A provider uses push delivery. It is separate
from the operator API token and must also contain at least 24 characters. Set the A2A connection's
`push_token_env` to `HOH_A2A_PUSH_TOKEN` and its callback URL to this same server's
`/v1/a2a/push` route. The provider sends that token as Bearer authorization; the token value is not
stored in the project.

Binding to a non-loopback address is fail-closed unless both `--tls-cert` and `--tls-key` are
provided. TLS 1.2 or newer is enforced. HoH does not generate or manage a public server certificate;
use a certificate appropriate for the network where the service is exposed.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/health` | Versioned liveness check |
| GET | `/v1/openapi.json` | OpenAPI 3.1 discovery |
| GET/POST | `/v1/projects` | List queue summaries or register a Git root |
| GET | `/v1/projects/{id}` | Read one project summary |
| GET | `/v1/projects/{id}/queue` | Read the persistent queue |
| GET | `/v1/projects/{id}/metrics` | Read token/cost/time/quality totals |
| GET/POST | `/v1/projects/{id}/chat` | Read or continue the selected Supervisor conversation |
| POST | `/v1/projects/{id}/plan` | Confirm materialization and enqueueing of the chat plan |
| POST | `/v1/projects/{id}/run` | Run the project's guarded queue loop |
| POST | `/v1/run-due` | Run every due registered project |
| POST | `/v1/a2a/push` | Accept an authenticated A2A StreamResponse event for a running task |

Example:

```bash
curl -H "Authorization: Bearer $HOH_SERVER_TOKEN" http://127.0.0.1:8765/v1/projects
curl -X POST -H "Authorization: Bearer $HOH_SERVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"root":"/home/me/project","name":"Project"}' http://127.0.0.1:8765/v1/projects
curl -X POST -H "Authorization: Bearer $HOH_SERVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Implement the agreed feature and verify it."}' \
  http://127.0.0.1:8765/v1/projects/PROJECT_ID/chat
```

Requests are limited to 1 MiB, responses contain no secret values, CORS is not enabled, and there is
no unauthenticated mutation endpoint. Long queue/model calls occupy only their request thread; the
server remains responsive while underlying HoH leases prevent unsafe overlap.

The A2A callback route uses the dedicated push token, not `HOH_SERVER_TOKEN`. Push delivery is
process-local by design: the callback must reach the same HoH server process that is waiting for the
remote task. Desktop calls use an automatically managed loopback receiver; SSE remains preferred
when the Agent Card advertises it.

## Linux and macOS background service

The desktop **Projects** page or CLI calls the same service manager:

```bash
bash scripts/hoh.sh workspace service-install --interval-minutes 1 --json
bash scripts/hoh.sh workspace service-status --json
bash scripts/hoh.sh workspace service-uninstall --json
```

Linux installs `~/.config/systemd/user/hoh-workspace.service` and `.timer`, then uses
`systemctl --user`. macOS installs `~/Library/LaunchAgents/com.hoh.workspace-scheduler.plist` and
uses `launchctl bootstrap` in the current GUI user domain. Both invoke `workspace run-due`; they do
not store credentials or duplicate the scheduler.
