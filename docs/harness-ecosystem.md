# Coding-agent harness ecosystem and HoH compatibility

[Русский](harness-ecosystem.ru.md) · **English**

Research snapshot: 2026-08-28.

## Conclusion

HoH must not equate agent independence with a generic executable name. A coding model, a coding-agent
harness, and a transport are different things:

- a model is an inference target such as DeepSeek, GPT, Claude, Gemini, Qwen, or a local model;
- a harness owns the agent loop, tools, file edits, commands, permissions, and session state;
- a transport lets HoH control that harness and observe completion.

The primary interoperability boundary is Agent Client Protocol (ACP) v1 over JSON-RPC. ACP provides
session creation, prompts, streamed updates, permission requests, cancellation, session configuration
including model selection, and a curated installation registry. HoH should consume this protocol and
registry instead of maintaining one product-specific adapter per harness.

"Support all" therefore means:

1. every conforming ACP agent can be discovered and used without a HoH code change;
2. non-ACP headless CLIs can join through a declared process profile;
3. raw model APIs can occupy model-only roles, but cannot be called a coding Worker until a harness
   supplies the agent loop and repository tools;
4. remote/cloud agents join through the implemented A2A v1 JSON-RPC boundary rather than terminal
   scraping; and
5. every advertised combination is run once before it is relied on. Finding an agent on PATH is
   not evidence that it works.

ACP, A2A, and MCP solve different boundaries. ACP is the local coding-client-to-agent transport in
HoH. A2A v1 JSON-RPC is the implemented remote boundary for Agent Card discovery and long-running
task delegation. MCP connects an AI
application or agent to tools, data, prompts, and workflows; it can equip a selected role but does
not replace HoH's role lifecycle, Git authority, evidence, or completion protocol.

## Current ACP ecosystem

The official ACP Registry returned 39 authenticated agent entries on the research date. Registry
versions change independently of HoH, so the IDs below are the stable integration keys and versions
must be read from the registry at discovery/install time.

| Group | Registry IDs |
|---|---|
| Major commercial coding agents | `claude-acp`, `codex-acp`, `cursor`, `factory-droid`, `github-copilot-cli`, `junie`, `poolside`, `devin` |
| Google and model-vendor agents | `antigravity-acp`, `gemini`, `grok-build`, `glm-acp-agent`, `kimi`, `mistral-vibe`, `qwen-code` |
| Open and multi-provider agents | `cline`, `goose`, `opencode`, `pi-acp`, `fast-agent`, `deepagents`, `kilo`, `stakpak`, `vtcode` |
| Additional registered agents | `agoragentic-acp`, `amp-acp`, `auggie`, `autohand`, `codebuddy-code`, `cortex-code`, `corust-agent`, `crow-cli`, `dimcode`, `dirac`, `harn`, `minion-code`, `nova`, `qoder`, `sigit` |

The registry currently distributes agents through three mechanisms:

- 19 entries through pinned `npx` packages;
- 16 entries through checksummed platform binaries;
- 2 entries offering both a binary and `npx`; and
- 2 entries through pinned `uvx` packages.

Every listed entry is protocol-compatible, but availability still depends on platform, installed
launcher, authentication, credentials, organization policy, and provider quota.

## Important harnesses and automation surfaces

| Harness | Programmatic surface confirmed by primary documentation | ACP registry | HoH target |
|---|---|---:|---|
| OpenAI Codex CLI | `codex exec`; stdin prompt; workspace selection; JSONL; output schema; sandbox controls | `codex-acp` adapter | ACP first, native exec fallback |
| Anthropic Claude Code | headless `-p`; stdin stream JSON; JSON Schema output; tool and permission controls | `claude-acp` adapter | ACP first, native headless fallback |
| Google Gemini CLI | non-interactive prompt/stdin; JSON or stream JSON; approval modes; native `--acp` | `gemini` | ACP |
| Google Antigravity CLI | current Google terminal harness replacing Gemini CLI for individual-account workflows | `antigravity-acp` | ACP |
| GitHub Copilot CLI | `-p`; silent output; model/agent selection; allow/deny tools; no-user-input mode | `github-copilot-cli` | ACP or native headless |
| Cursor Agent CLI | `-p`; text/JSON/stream JSON; `--force` for edits | `cursor` | ACP or native headless |
| Kiro CLI | `chat --no-interactive`; stream JSON; scoped tool trust; stable exit codes | listed by ACP agents documentation | ACP when installed, native headless fallback |
| Factory Droid | `droid exec`; stdin/file prompts; structured output; autonomy levels | `factory-droid` | ACP or native headless |
| Cline CLI | headless stdin/prompt and JSON events | `cline` | ACP |
| OpenCode | `run`; model and agent selection; JSON events; headless server | `opencode` | ACP |
| OpenHands | headless task/file input and JSON output; ACP mode; WSL requirement on Windows | ACP-capable, not in the current authenticated registry snapshot | explicit ACP command |
| Qwen Code | prompt/stdin; JSON/stream JSON; exit codes; OpenAI-compatible providers including DeepSeek | `qwen-code` | ACP |
| Kimi Code CLI | `-p`; stream JSON; native ACP; model/agent selection | `kimi` | ACP |
| Hermes Agent | ACP, JSON-RPC TUI gateway, and HTTP/SSE server | ACP-capable | ACP |
| Goose | recipes/headless execution and agent service | `goose` | ACP |
| Aider | one-shot `--message`/`--message-file`; can use DeepSeek; edits and commits by default | no current ACP registry entry | declared process profile with auto-commit disabled |
| Crush | multi-provider terminal harness, including OpenAI-compatible models | no confirmed stable headless/ACP contract in primary docs reviewed | not advertised until a machine contract is verified |
| Roo Code and IDE-only extensions | editor-hosted interaction | no verified standalone machine contract | require ACP/headless bridge; no UI automation claim |

This table is intentionally capability-based. Product popularity does not make an interactive TUI a
safe automation interface.

## DeepSeek is a model/provider, not one harness

There is no single canonical "DeepSeek harness" in the ACP Registry. DeepSeek can be used in three
different ways:

1. as a model selected inside an ACP harness that supports DeepSeek or an OpenAI-compatible provider,
   such as OpenCode or Qwen Code;
2. through a non-ACP harness such as Aider; or
3. directly through the DeepSeek API for a model-only, read-only role.

For the requested combination, the intended portable configuration is:

- Worker: `codex-acp`, default or explicitly selected Codex model, ACP permissions allowed only in
  the isolated attempt worktree;
- Critic: an independent ACP harness such as `opencode` or `qwen-code`, with its ACP model option set
  to the configured DeepSeek model, all tool permissions rejected, and exact judgment JSON required;
- alternative Critic: direct DeepSeek API through a model-JSON critic driver, also with no tools or
  repository write access.

The harness and model identities must both be recorded in the role profile and audit evidence. Saying
only `critic = deepseek` is ambiguous and is not enough for reproducibility.

## Protocol coverage required in HoH

### Tier 1: ACP v1 and Registry

This is the default path for local coding agents:

- fetch and cache the official registry explicitly;
- retain registry ID, version, distribution, command, arguments, hashes, and source URL;
- support `npx`, `uvx`, and checksummed platform-binary distributions;
- initialize the ACP connection and inspect capabilities/authentication methods;
- create an isolated session with an explicit `cwd`;
- select model/mode/reasoning through ACP session config options when requested;
- mediate permissions by role: bounded allow for Worker, reject for Critic;
- capture session updates, tool calls, final messages, stop reason, usage, and errors;
- cancel and close sessions on timeout;
- never treat `cwd` as a sandbox by itself; HoH's isolated worktree and OS process boundary remain
  mandatory.

### Tier 2: Declarative headless process profiles

For agents without ACP, a versioned profile must describe:

- executable resolution on Windows, Linux, and macOS;
- prompt transport: stdin text, argv, JSONL, or temporary file;
- working-directory behavior;
- model and permission argument templates;
- output contract: text, JSON, JSONL, or filesystem diff;
- exit-code semantics, timeout, cancellation, and session persistence;
- forbidden arguments and required safety arguments; and
- whether the harness commits automatically and how that is disabled.

A single `command + args` tuple cannot express these differences safely.

HoH 1.0.0 retains the Worker subset needed by one-shot filesystem-diff CLIs: stdin or temporary
file prompts, fixed/forbidden arguments, optional model argument, version probe, bounded exit and
timeout handling, and HEAD-preserving isolated diff collection. Argv prompt bodies, JSONL process
streams, persistent sessions, and non-diff artifacts remain future profile protocol versions rather
than undocumented behavior in v1.

### Tier 3: Direct model drivers

Direct model access is suitable for Supervisor planning, semantic verification, and a read-only
Critic. It needs provider-neutral structured output for OpenAI Responses, OpenAI-compatible Chat
Completions (including DeepSeek), Anthropic, Gemini, and loopback/local endpoints. A raw model is not
a Worker unless a trusted executor provides tools and applies changes through HoH's attempt contract.

### Tier 4: Remote asynchronous agents

Cloud agents use A2A v1 Agent Cards and JSON-RPC. HoH supports `SendMessage`, `GetTask`, and
`CancelTask`, prefers `SendStreamingMessage` plus `SubscribeToTask` over SSE when advertised, and
can attach authenticated task push-notification configuration. It accepts immediate messages,
stream events, pushed updates, or polled tasks, extracts bounded text/data artifacts, and rejects
oversized or malformed payloads.

Bearer/API-key values still come from environment variables. OAuth2 client credentials and OIDC
device authorization use the endpoints configured by the operator or advertised by the required
Agent Card security scheme. The client registration itself remains provider/account-specific: HoH
does not invent it. Device-flow access and refresh tokens are stored outside project Git in the
current user's token cache; Windows records are protected with current-user DPAPI. Run
`a2a-login --config .hoh/harness.json --name <agent>` or use the GUI login action, then run
`a2a-test` for the same profile.

## Implemented compatibility boundary

HoH 1.0.0 implements the complete local and remote three-role boundary:

- generic ACP drivers exist independently for Supervisor, Worker, and Critic;
- Supervisor also supports direct structured models (`model_json`) and generic one-shot processes
  (`command_json`), while HoH retains all Git and state authority;
- Worker runs only in an isolated `hoh/attempt/*` worktree and returns a filesystem diff;
- Critic runs in an empty isolated directory, rejects ACP tool permissions, and must return exact
  judgment JSON;
- requested ACP models are selected through `session/set_config_option` and fail closed when absent;
- `role-catalog --refresh-registry` explicitly fetches and caches the official registry, while
  `agent-manage` and the desktop UI install exact pinned npm/uv packages and checksummed archives
  atomically into the current user's HoH directory;
- ACP-advertised authentication and supported vendor CLI login flows are available without HoH
  receiving or persisting the vendor token;
- project-configured A2A v1 agents can independently fill Supervisor, Worker, or Critic. The remote
  Worker receives only explicitly allowed UTF-8 text files and must return a locally validated diff;
- A2A OAuth2/OIDC login, token refresh, SSE task streaming/subscription, authenticated push
  configuration, and a bounded push receiver are implemented without storing secrets in project
  configuration;
- a registry ID can be placed directly in any of the three role slots without a TOML agent entry;
- registry environment settings are preserved and Windows `.CMD` shims are resolved explicitly;
- Windows ACP process trees are terminated as a unit so child agents cannot retain worktrees;
- missing role drivers and unavailable registry distributions fail during role validation.

Live evidence on Windows/Python 3.11.15, recorded 2026-08-28 against an earlier controller
build: `codex-acp` 1.7.0 with `gpt-5.4` independently passed all three role paths. Supervisor
produced a validated project spec; Worker completed an isolated agent turn, diff collection,
deterministic checks, and a Supervisor-owned commit; Critic returned a closed decision attesting
the immutable commit and patch hashes. Every disposable canonical worktree remained clean.

**That result says nothing about your machine.** It was one account, one agent version and one
controller build. Treat every agent as protocol-supported and otherwise unproven until
`role-conformance --role <role>` succeeds where you are.

Evidence boundaries that intentionally remain:

- a Registry binary without a published SHA-256 is not auto-installed; the operator may install it
  independently on PATH after applying their own supply-chain policy;
- provider quota, ACP model names, organization policy, OAuth client registration, callback DNS/TLS,
  and provider consent remain account/service-specific;
- direct model-JSON Critic transport is implemented; using it still requires credentials for the
  exact model and account;
- declarative one-shot Worker process profiles are implemented, including stdin/file prompt
  placement, mandatory and forbidden arguments, model routing, secret-argument rejection, and
  fail-closed detection of Worker-owned commits; Aider is the first bundled declaration, and every
  additional CLI or model still has to be run once before it is trusted.

## Implementation roadmap and acceptance bar

1. **Implemented:** generic ACP Supervisor, Worker, and Critic drivers.
   - verified by unit/integration coverage and live Codex conformance for all three roles on Windows.
2. **Implemented:** ACP Registry discovery, cache, pinned launch resolution, and managed install.
   - npm/uv installs are exact and user-scoped; archives require HTTPS plus SHA-256 and bounded safe
     extraction; unsafe manifests remain blocked rather than silently downgraded.
3. **Implemented:** role target records agent/model/driver and resolves registry source/version.
4. **Implemented:** ACP model selection and authentication UX fail closed on missing declarations.
   - exact live behavior still requires per-agent conformance because vendor accounts differ.
5. **Implemented:** direct provider-neutral model Critic, including DeepSeek configuration.
   - verified by closed-schema approve/error tests, credential preflight, canonical decision
     normalization, and role-profile/GUI target resolution; each live provider still has to be
     tried with its own credentials.
6. **Implemented:** declarative headless Worker process profiles for non-ACP agents, starting with
   Aider.
   - verified by configuration round-trip, stdin and temporary-file prompt transports, mandatory /
     forbidden/model arguments, secret-argument rejection, timeout/exit handling, isolated diff
     collection, Supervisor-owned commit, and a regression that rejects any Worker HEAD movement.
   - whether Aider works depends on the environment, model and account; a bundled declaration only
     means HoH knows how to call it.
7. **Implemented:** an agent matrix that reports what is installed here.
   - status values are `available` and `unavailable`, and nothing more is claimed. An earlier
     design kept signed certification records bound to a hash of the controller's own source. It
     was removed: the records outlived the code they attested, and the documentation went on
     claiming a certification the build no longer had. `role-conformance` answers the same question
     honestly, by running the role now.
8. **Implemented for 1.0.0:** portable artifacts, documentation, desktop/headless daily-operation setup, and
   A2A remote role support.
   - verified with the current source suite, compileall, a clean final audit, manifest/hash checks, cold
     extracted embedded Doctor, protocol/three-head conformance, and all four GUI locale/theme
     smokes, plus an end-to-end three-role cycle over a real repository. The GUI covers
     process-profile editing, session credentials, whole-queue execution, history, retry, and
     recovery. Using DeepSeek directly still needs its own credentials, as in item 5.

## Primary sources

- ACP introduction and architecture: <https://agentclientprotocol.com/get-started/introduction>
- ACP Registry and current agent list: <https://agentclientprotocol.com/get-started/registry>
- ACP-compatible agents: <https://agentclientprotocol.com/get-started/agents>
- ACP Python SDK: <https://agentclientprotocol.com/libraries/python>
- ACP session config options: <https://agentclientprotocol.com/rfds/session-config-options>
- OpenAI Codex CLI: <https://developers.openai.com/codex/cli/reference/>
- Claude Code repository and headless surfaces: <https://github.com/anthropics/claude-code>
- Gemini CLI automation: <https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/tutorials/automation.md>
- GitHub Copilot CLI programmatic reference: <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference>
- Cursor headless CLI: <https://docs.cursor.com/en/cli/headless>
- Kiro headless CLI: <https://kiro.dev/docs/cli/headless/>
- Factory Droid Exec: <https://docs.factory.ai/droid-exec/overview>
- Cline CLI: <https://github.com/cline/cline>
- OpenHands CLI: <https://docs.openhands.dev/openhands/usage/cli/quick-start>
- OpenCode CLI: <https://opencode.ai/v2/docs/cli>
- Qwen Code headless mode: <https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/headless.md>
- Kimi CLI command reference: <https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command>
- Hermes programmatic integration: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/programmatic-integration.md>
- Aider scripting and DeepSeek: <https://aider.chat/docs/scripting.html>, <https://aider.chat/docs/llms/deepseek.html>
- A2A protocol overview and specification: <https://a2a-protocol.org/latest/>, <https://a2a-protocol.org/latest/specification/>
- MCP introduction: <https://modelcontextprotocol.io/docs/getting-started/intro>
