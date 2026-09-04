# Agent-neutral drivers

[Русский](agent-neutral-drivers.ru.md) · **English**

## Current boundary

HoH core does not select behavior from product names such as Hermes, Claude, Codex, or OpenClaw.
It selects an explicit, versioned driver contract for each of Supervisor, Worker, and Critic.
Every conforming ACP Registry agent can fill any of those roles without a HoH code change. Runtime
availability still requires its launcher, authentication, credentials, and model configuration.

The ecosystem audit found that ACP plus the official ACP Registry is the appropriate universal
coding-agent boundary. See `docs/harness-ecosystem.md`. Non-ACP one-shot CLIs use either the simple
stdin `command` contract or the declarative `process` contract; neither declaration is live evidence
until that exact executable/model passes Worker conformance.

Remote agents use the product-neutral A2A v1 boundary. A profile may authenticate with bearer,
API-key, OAuth2, or OIDC; prefer SSE task streaming; subscribe to a long-running task; and request an
authenticated push callback. OAuth client registration remains supplied by the provider/operator,
while HoH owns discovery, device login, refresh, redaction, and user-scoped token storage. These
features are identical for Supervisor, Worker, and Critic; role-specific authority is enforced by
the adapter around the transport rather than by an agent product name.

For a desktop loopback callback, the A2A client starts an authenticated receiver for the duration of
the request. For an externally reachable callback, run the task in HoH server mode and point the
provider at `/v1/a2a/push`; the server route and the waiting adapter share one process-local inbox.

Refresh and inspect the official ACP Registry cache:

```powershell
.\scripts\hoh.ps1 role-catalog --refresh-registry --json
```

Inspect HoH's installed driver contracts:

```powershell
.\scripts\hoh.ps1 driver-catalog --json
```

Every entry uses protocol `hoh.driver`, version `1.0`, and declares its role, runtime adapter,
transport, artifact contract, isolation requirement, subagent capability, and safety profile. The
machine schema is `schemas/driver-manifest-v1.schema.json`.

## Supervisor boundary

Supervisor is no longer identity-only. It has three execution contracts:

- `model_json`: the configured `[supervisor_model]` returns a schema-constrained project plan;
- `command_json`: a generic process reads the planning prompt from stdin and emits project-spec JSON;
- `acp`: an ACP harness plans in an empty temporary directory under the Supervisor MCP policy;
  the default rejects every tool permission and supplies no MCP server.

The selected Supervisor can reason and produce a plan, but it does not own repository mutation.
HoH's deterministic control plane still owns queue transitions, patch application, verification,
commits, rollback, audit, and handoff.

## Worker boundary

Use `driver = "command"` for any CLI agent that can:

1. start non-interactively in the current working directory;
2. read one constrained task prompt from stdin;
3. edit files in that directory;
4. exit `0` after producing candidate changes; and
5. avoid commit, branch, merge, push, approval, and customer communication.

HoH creates an isolated `hoh/attempt/*` worktree, provides `HOH_*` metadata environment variables,
collects the resulting Git diff, deletes the attempt, verifies the patch, and alone applies and
commits it to the canonical repository. Worker stdout is diagnostic only; it is not trusted as the
artifact.

```toml
[worker]
driver = "command"
command = "my-agent-cli"
args = ["run"]
timeout_seconds = 300

[[agents]]
name = "my-agent"
command = "my-agent-cli"
args = ["run"]
worker_driver = "command"
```

The agent name is inventory/UI metadata. `worker_driver` is the execution contract. Renaming the
agent or executable does not change driver selection.

### Declarative one-shot process profiles

Use `driver = "process"` when the CLI needs its prompt in a file or requires fixed non-interactive
and safety arguments. The profile is data in project configuration, not Python code. It declares:

- `prompt_transport`: `stdin` or `file`;
- `prompt_argument`: the HoH-owned flag placed before a temporary prompt path for `file` transport;
- `required_args`: flags HoH always appends;
- `forbidden_args`: operator args that would defeat the safety contract;
- `model_argument`: the HoH-owned model selector, when supported; and
- `version_args`: the non-mutating version probe arguments.

Example custom Worker in `.hoh/harness.json`:

```json
{
  "agents": [
    {
      "name": "my-headless-cli",
      "command": "my-headless-cli",
      "args": ["run"],
      "worker_driver": "process",
      "worker_process_profile": {
        "profile_id": "my-headless-cli-v1",
        "prompt_transport": "file",
        "prompt_argument": "--prompt-file",
        "required_args": ["--non-interactive", "--no-commit"],
        "forbidden_args": ["--commit", "--interactive"],
        "model_argument": "--model",
        "version_args": ["--version"]
      }
    }
  ]
}
```

HoH rejects configured arguments that collide with the prompt/model flags, match the profile's
forbidden flags, or carry common inline secret options such as `--api-key` and `--token`. Put
credentials in the launching process environment. The temporary prompt file is removed after the
process exits; stdout/stderr are bounded diagnostic evidence, while the filesystem diff remains the
only candidate artifact.

The default catalog includes Aider as a declarative Worker profile. Its current profile follows the
documented one-shot scripting contract: `--message-file`, `--no-stream`, `--yes`,
`--no-auto-commits`, and `--no-dirty-commits`; an optional role model is passed with `--model`.
The profile appears as selectable only when `aider` is on `PATH`. HoH does not store provider keys or
install Aider automatically.

For every isolated Worker driver, HoH records the attempt's starting commit and rejects the result
if the Worker moves Git `HEAD`. This prevents an auto-committing CLI from turning its changes into an
empty collected diff and preserves Supervisor-only commit ownership independently of CLI flags.

## Critic boundary

Use `driver = "model_json"` for a direct provider-neutral Critic. It invokes `[verifier_model]`
with the same immutable evidence and closed judgment schema, passes no repository path, and records
only redacted request/response evidence. HoH supports `openai`, `deepseek`, and an explicit
`openai_compatible` HTTPS endpoint.

```toml
[verifier_model]
provider = "deepseek"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"

[critic]
driver = "model_json"
command = ""
```

Use `driver = "command_json"` for any read-only reviewer CLI. HoH sends the immutable review prompt
and bundle on stdin. The process must write one JSON object on stdout with exactly these fields:

- `decision`: `approve`, `reject`, or `escalate`;
- `summary`;
- `findings`;
- `correction_brief`; and
- `escalation`.

HoH validates the judgment and attaches trusted bundle ids, hashes, timestamps, identity, and
attestation itself. The critic must not edit files, invoke project tools, or commit.

```toml
[critic]
driver = "command_json"
command = "my-reviewer-cli"
args = ["review"]
timeout_seconds = 300
```

`manual` preserves the file export/import boundary. `claude_code` is a bundled edge driver that
enforces plan mode, no tools, and no session persistence.

Use `driver = "acp"` to select any ACP agent as Critic. HoH starts it outside the repository,
applies the Critic MCP policy (reject/no servers by default), optionally selects the requested ACP model, and accepts only the closed
judgment JSON contract.

## Model-provider boundary

Supervisor, semantic-verifier, and direct-Critic model roles support `stub`, `openai`, `deepseek`, and
`openai_compatible`. An OpenAI-compatible endpoint uses the Chat Completions JSON contract and must
declare both `base_url` and `api_key_env`; credentials are never stored in config.

```toml
[supervisor_model]
provider = "openai_compatible"
model = "vendor-model"
base_url = "https://models.example.test/v1"
api_key_env = "VENDOR_MODEL_API_KEY"
```

HTTPS is mandatory except for loopback test endpoints.

## Role profiles and compatibility

Role profile v2 can carry an explicit `driver` beside `agent` and `model`. If omitted, HoH reads
the explicit `supervisor_driver`, `worker_driver`, or `critic_driver` from the selected catalog
entry. It never
infers a driver from an agent name or command basename.

Legacy role profile v1 remains readable. Legacy `[worker].type` and `[critic].type` remain accepted
aliases. New configurations should use `driver`; if both keys are present they must resolve to the
same canonical driver.

Example with three independent choices:

```json
{
  "profile_version": "2.0",
  "critic_enabled": true,
  "max_attempts": 3,
  "roles": {
    "supervisor": {"agent": "claude-acp", "model": null, "driver": null},
    "worker": {"agent": "codex-acp", "model": null, "driver": null},
    "critic": {"agent": "qwen-code", "model": "<exact DeepSeek ACP option>", "driver": null}
  }
}
```

The agent IDs resolve from the cached official registry. The model string is not guessed: HoH asks
the selected ACP session for its model options and fails if the requested exact value is absent.
Thus Codex can be Worker while a different DeepSeek-capable harness is Critic, but DeepSeek alone is
a model name, not a harness identity. For a harness-free Critic, select the catalog target
`direct-critic-model`; its provider/model identity comes from `[verifier_model]`.

## Adding an agent without core changes

1. For ACP, run `role-catalog --refresh-registry` and use the registry ID directly. For a non-ACP
   process, add an `agents[]` entry with command, args, environment, and explicit role drivers. Use
   `worker_process_profile` if prompt placement or mandatory safety flags differ from stdin.
2. Run `doctor` to verify configuration and executable discovery.
3. Run `worker-smoke` for a non-live preflight.
4. Run `worker-conformance --config ...` or `worker-smoke --live` in the disposable repository.
5. Only then use the driver on a real project.

If an agent cannot satisfy `command` or `process`, add a new edge adapter and a versioned registry
entry. Do not
import arbitrary third-party Python into the Supervisor process: keep provider credentials,
sessions, and agent-specific safety logic across the process boundary.
