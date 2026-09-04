# HoH 1.0.0

[Русский](RELEASE_NOTES.ru.md) · **English**

HoH is a local desktop and headless control plane for a three-role AI workflow:
Supervisor, Worker, and Critic. Each role can use its own agent, model, and
communication driver.

## What to download

| Platform | Asset | Notes |
|---|---|---|
| Windows 10/11 x64 | `HoH-Setup-1.0.0.exe` | Per-user installer. Authenticode is unsigned, so Windows SmartScreen may warn about an unknown publisher. |
| Ubuntu/Debian x86_64 | `hoh_1.0.0_amd64.deb` | Install with the system package manager. Only `git` is a dependency. |
| macOS x86_64 | `HoH-1.0.0.dmg` | Drag `HoH.app` to Applications. This build is ad-hoc signed, so Gatekeeper blocks the first launch: on macOS 15 and newer, open **System Settings → Privacy & Security** and press **Open Anyway** under the message about HoH; on older macOS, Finder → right-click → **Open**. |

All three packages carry their own Python and Tk. Nothing has to be installed
first, and no network access is needed to install them.

Upload the matching `.sha256` sidecars and the Windows manifest together with
the installers. Those sidecars hold the authoritative SHA-256 values; verify
them before installing:

```bash
sha256sum -c hoh_1.0.0_amd64.deb.sha256
shasum -a 256 -c HoH-1.0.0.dmg.sha256
```

## Included in 1.0.0

- Desktop GUI and authenticated headless server for Windows, Linux, and macOS.
- Russian and English UI, light and dark themes, responsive layout, and a guided first-run wizard.
- Independent Supervisor, Worker, and Critic role assignment with generic ACP v1 support.
- ACP Registry refresh, user-scope managed agent installation, vendor login handoff, and fail-fast availability checks.
- Remote A2A v1 connections with Agent Card discovery, bearer/API-key/OAuth2/OIDC authentication, SSE streaming, authenticated push, and bounded polling fallback.
- Per-role MCP policies, project queue and history, retries and recovery, metrics, scheduled runs, desktop notifications, and optional Telegram notifications.
- Signed side-by-side update catalog, compatibility evidence, Doctor, protocol conformance, and final audit commands.

## What was verified

Every package was built from this commit and then installed and run on its own
operating system, rather than only on the machine that produced it:

| | Windows 11 Pro | Ubuntu 26.04 | macOS 15.1 |
|---|---|---|---|
| Test suite | 559 passed | 559 passed | 559 passed |
| Installed from the artifact | silent install, clean profile | `dpkg -i` | DMG mounted, signature verified, copied to Applications |
| CLI after install | `doctor` ok on a config with a byte order mark | same | same |
| Conformance after install | `protocol-conformance`, `three-head-conformance`, `workflow-smoke` all ok | same | same |
| GUI smoke, locale × theme | 4 of 4 | 4 of 4 | 4 of 4 |
| Embedded interpreter | Python 3.11.15, Tk 8.6 | Python 3.11.16, Tk 9.0 | Python 3.12.10, Tk 8.6 |

The Windows uninstaller was also run: it leaves no directory, no Start-menu
shortcut, and no entry in the installed-programs list.

Running these checks on the real operating systems is what found the last three
defects in this release: no package carried the third-party notices, the release
scripts could not be run from the PowerShell that ships with Windows, and inside
the macOS bundle every GUI operation opened a second copy of the application
instead of running the command. All three are fixed in the artifacts above.

The Linux package runs on glibc 2.17 and newer, so Ubuntu 20.04 and Debian 11
and later are covered.

## What is not claimed

- **A package that starts is not a working vendor account.** Whether a given
  agent works for you is answered by running `role-conformance --role <role>` on
  your own machine, not by this release.
- Windows Setup is not Authenticode-signed and the macOS build is not notarized.
  Both warnings above are expected for a build without a paid signing identity.
- No live three-role run against a commercial provider is included in this
  release's evidence; the last attempt stopped at a provider quota limit.

## Third-party components

The installers embed CPython, Tcl/Tk, OpenSSL, SQLite, zlib and several smaller
libraries, each under its own licence. They are listed in
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md). HoH's own source has no
third-party runtime dependencies.

## A2A deployment note

Local ACP does not require a domain, public TLS certificate, or OAuth client.
Remote A2A only requires those pieces when a provider must reach HoH from
outside the machine. HoH validates HTTPS for non-loopback endpoints and can
terminate TLS when the operator supplies a certificate and key; DNS, public
certificate issuance, and provider OAuth client registration remain operator-
or provider-side configuration.

## License

HoH is released under the Apache License 2.0. Copyright 2026 Sergey Lebedev.
See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
