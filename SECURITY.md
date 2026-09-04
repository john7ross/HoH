# Security policy

[Русский](SECURITY.ru.md) · **English**

## Scope

HoH is a local control plane. Cloud model accounts, vendor sessions, and
remote A2A services remain outside the application and keep their own security
policies.

## Security boundaries

- Secret values are not written to project configuration, role profiles,
  review bundles, metrics, or journals. Configuration stores environment-variable
  names only, so a repository cannot leak a credential.
- A credential you explicitly save in the interface is kept in your user profile,
  outside any project: DPAPI-protected on Windows, mode 0600 elsewhere. An
  exported environment variable always takes precedence over a saved one.
- Worker execution is isolated in a temporary Git worktree. Worker and Critic
  cannot commit, merge, push, or close a task; Supervisor-owned Git integration
  is the only commit path.
- Patches, allowed paths, verification commands, review decisions, and Git
  identity are bound to immutable evidence. A moved attempt `HEAD` fails closed.
- State and repository execution use separate leases. Corrupted or ambiguous
  operation records block new mutation until an operator resolves them.
- A2A external endpoints require HTTPS. Headless non-loopback binding requires
  an operator-supplied TLS certificate and key and a strong API token. Push
  callbacks require a separate token.
- Managed agent archives are HTTPS-only when downloaded, require Registry
  checksums, and are extracted with traversal, link, device, file-count, and
  size limits.
- Update packages are accepted only through a trusted signed catalog with
  exact platform, size, and SHA-256 checks.

## Reporting a vulnerability

Report privately to **Sergey Lebedev <john.spb.ross@gmail.com>**, or through the
repository's private security advisories at <https://github.com/john7ross/HoH/security/advisories/new>. Do not
publish credentials, tokens, private A2A URLs, or a working exploit in a public
issue.

Include a description, the affected version and commit, reproduction steps, the
impact you expect, and a safe contact address.

Expect an acknowledgement within 7 days and an assessment within 30 days. Please
hold public disclosure until a fixed release is available, or for 90 days from
the report, whichever comes first.

## Operator checklist

Use a dedicated environment for provider credentials, keep `*.env`, keys, and
certificates outside Git, bind the headless API to loopback unless external
access is required, rotate API/push tokens after sharing a server, and retain
the queue/lease/audit evidence needed to investigate a failed run.
