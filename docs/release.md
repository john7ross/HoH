# HoH production release

[Русский](release.ru.md) · **English**

## Release boundary

The canonical product source is the Git repository at `C:\path\to\repo`. End-user releases
are native installable applications:

- Windows x64: `dist/HoH-Setup-<version>.exe`, built by `scripts/package-windows-installer.ps1`;
- Ubuntu/Debian: `dist/hoh_<version>_<architecture>.deb`, built by `scripts/package-deb.sh` on Linux;
- macOS: `dist/HoH-<version>.dmg`, built by `scripts/package-macos.sh` on macOS.

All three packages carry their own Python and Tk; the Debian package declares only Git. Users do
not extract source archives or invoke Python. Operational state, review
evidence, operation ledgers, attempt worktrees, configuration, and credentials are outside the
application installation.

### Where each platform can be maintained from

Running HoH is supported on all three platforms. Producing and publishing a release is not
symmetric, and this release does not pretend otherwise:

| Task | Windows | Linux | macOS |
|---|---|---|---|
| Run the GUI, CLI and headless server | yes | yes | yes |
| Daily operations (`scripts/run-daily-ops.*`) | yes | yes | yes |
| Workspace scheduler registration | yes | yes (systemd user timer) | yes (LaunchAgent) |
| Build the platform's own package | yes | yes | yes |
| Build the internal update payload (`build-update-payload.ps1`) | yes | no | no |
| Sign the update catalog (`sign-catalog.ps1`) | yes | no | no |
| Register the Telegram watch task | yes | no | no |

The update channel and its signing therefore have to be produced from Windows. That is a
maintainer constraint, not a user constraint: an installed Linux or macOS build consumes the
signed catalog normally.

HoH does not require a paid public code-signing certificate. Windows Setup is unsigned, so
SmartScreen reputation is not claimed. The macOS build uses ad-hoc signing, so Gatekeeper can show
an unidentified-developer warning until a Developer ID/notarization is supplied. The application
update channel independently uses an explicitly trusted self-signed RSA publisher identity, a
signed static JSON catalog, and exact package size/SHA-256.

HoH 1.0.0 provides user-scope integrity-checked agent/application installation, guided vendor
authentication, three independent roles, A2A OAuth/OIDC streaming/push, per-role MCP policy,
multi-project scheduling, metrics, trusted updates, and desktop/headless operation. It retains
generic ACP Registry support and declarative one-shot Worker profiles.

HoH claims nothing about whether a given agent works for you. `compatibility-matrix` reports what
is installed on this machine:

```
python -m llm_harness compatibility-matrix --project-root . --json
```

To find out whether a role actually runs, run it: `role-conformance --role <role>` executes it once
in a throwaway repository. Native installers must be smoke-tested on their own operating systems.
The release sidecar is authoritative for the exact source commit and package hash.

`compatibility-matrix` describes the machine it runs on and nothing else. After installing on a new
target, run `role-conformance` there for each role you intend to rely on.

Managed agents live under `%LOCALAPPDATA%\HoH\agents` and are not bundled into the application.
Vendor sessions remain in each vendor's own credential store. Project A2A connection metadata
is stored in `.hoh/harness.json`; bearer/API-key values and OAuth client secrets are
environment-only and are not released. A2A device-flow tokens are stored separately under
`%LOCALAPPDATA%\HoH\oauth-tokens`, protected with current-user Windows DPAPI, and are never bundled.

## Build from a clean commit

Windows:

```powershell
cd C:\path\to\repo
git status --short
$env:PYTHONPATH = (Resolve-Path .\src).Path
runtime\python\python.exe -m unittest discover -s tests
runtime\python\python.exe -m compileall -q src tests
.\scripts\hoh.ps1 driver-catalog --json
.\scripts\hoh.ps1 protocol-conformance --distribution-root . --json
.\scripts\hoh.ps1 three-head-conformance --json
.\scripts\package-windows-installer.ps1
```

Ubuntu/Debian and macOS must be built from the same clean commit on their target systems:

```bash
bash scripts/bootstrap-runtime.sh  # Linux only: fetch the interpreter the package ships
bash scripts/package-deb.sh        # Linux only
bash scripts/package-macos.sh      # macOS only
```

`git status --short` must be empty before packaging. Each builder runs the source unit suite,
compile check, and Doctor. The Windows builder additionally refreshes and audits the embedded
runtime before compiling Setup. The macOS builder requires pinned PyInstaller on the release Mac;
PyInstaller is a build dependency and is not required by end users.

Native packagers run Doctor with an isolated temporary `release-smoke` executable candidate so a
clean builder can validate the package without requiring the user's agent CLIs. This probe is not
written to the application configuration or shipped in the package.

### Cross-platform packaging evidence (2026-09-03)

All three artifacts were built from the source published as `v1.0.0` and installed on the operating
system they target. None of them was produced by CI. This names the tag rather than a commit hash on
purpose: the publication history is squashed into a single commit each time, so a hash written here
would point at nothing a reader could look up.

They replace an earlier v1.0.0 upload that nobody had downloaded. The version did not change
because nothing in it did: the rebuild carries one fix to the shipped product, a Windows headless
server that would bind a port another process was already listening on instead of refusing to
start, plus an audit report that says why a verification command failed. Replacing published assets
is only honest while the digests here and in the release notes are replaced with them, and while
the tag points at the source they were built from.

| Artifact | Bytes | SHA-256 |
|---|---|---|
| `HoH-Setup-1.0.0.exe` | 17,887,456 | `2136e2aa446d9a1b807e78cf6a3767e3442b02a742cbbde32ba272f8af7ac762` |
| `hoh_1.0.0_amd64.deb` | 30,008,884 | `6e0275e086ebed1b3b3b74116f8537de2d1875fc83d2ca67e56fa6205ffb8c20` |
| `HoH-1.0.0.dmg` | 16,063,324 | `644a48d628674dc38acdc768d372f6ed449f4c82aca13b033bf679168d5f6591` |

A digest is computed after the artifact exists, so the copy of this table inside those artifacts
lists the previous round's values. The `.sha256` sidecars published beside each package are the
authority; this table is here so the evidence and the digests sit in one place.

Each installed product was exercised the same way: `doctor` with no configuration at all, from a
directory that is the user's own repository rather than a checkout of HoH; `doctor` on a UTF-8
config with a byte order mark; the GUI smoke in both locales and both themes; and
`protocol-conformance`, `three-head-conformance` and `workflow-smoke`. The first of those is new:
every earlier round passed a stub configuration that set `require_embedded_python` to false, which
switched off the check that failed for an installed Windows user. All returned `ok: true`, and the GUI smoke passed
4 of 4 on each platform.

Windows 11, per-user silent install to `%LOCALAPPDATA%\Programs\HoH`: embedded CPython 3.11.15 and
Tk 8.6; no compiled bytecode outside the interpreter; `THIRD-PARTY-NOTICES` and all 18 Russian
documents present. The uninstaller left no install directory, no Start Menu folder and no
uninstall registry key.

The Debian package grew by five megabytes in this round: the interpreter's standard library is now
compiled during the build, with the installed path recorded, rather than shipped as the build tree
left it. `/opt` belongs to root, so a user cannot cache bytecode there; without it every start would
recompile the standard library and throw the result away.

Ubuntu 26.04, `dpkg -i`: embedded CPython 3.11.16 and Tk 9.0, so `git` is the only dependency;
nothing world-writable; no compiled bytecode from the build machine and no file naming its path;
`LICENSE`, `NOTICE`, `THIRD-PARTY-NOTICES`, `/usr/share/doc/hoh/copyright` and all 18 Russian
documents present. The `.sha256` sidecar names the file only, so `sha256sum -c` succeeds from the
package directory on any machine.

macOS, DMG mounted and the application copied to `/Applications`: the signature verifies with
`codesign --verify --deep --strict`, and `THIRD-PARTY-NOTICES` and all 18 Russian documents are
inside the bundle.

Three defects were found by running these checks and are fixed in the artifacts above: the
packages carried no third-party notices, the release scripts could not be run from the PowerShell
that ships with Windows, and inside the macOS bundle every GUI operation opened a second copy of
the application instead of running the command.

### Earlier cross-platform evidence (2026-08-29, superseded)

Those native artifacts were built from the commit current at the time and smoke-tested on each
target. They predate the 1.0.0 release work and are recorded only as history:

- Windows x64: the exact Codex ACP/gpt-5.4 attempt reached the provider but was rejected by the
  Codex usage limit.
- Ubuntu 26.04 x86_64: the current DEB installed successfully; packaged `doctor` and
  `hoh-gui --smoke` passed. After installing user-scoped Node.js 22.14, Supervisor, Worker, and Critic all
  reached the ACP adapter but returned `Authentication required`.
- macOS 15.1 x86_64: the current DMG passed target-side ad-hoc codesign, CLI, license/notice, and
  GUI smoke checks. After installing user-scoped Node.js 22.14, all three Codex ACP roles returned
  `Authentication required`.

A package that starts is not a working vendor account. Neither result says anything about whether
Codex works on Ubuntu or macOS for you; only a `role-conformance` run on your machine does.

## Publishing a release

Releases are published by hand, from packages built and installed on the operating system each one
targets. That is the point: the published bytes are the ones that were actually installed and run,
not ones a runner produced and nobody opened.

Nothing publishes automatically. `.github/workflows/release.yml` builds the three packages on clean
runners and leaves them attached to its own run, and it starts only when you ask for it, from
**Actions → Build native packages → Run workflow**. Use it to check that the packaging scripts still
work on a machine that is not yours; do not use its artifacts as the release.

It used to start on a `v*` tag and create the release itself. Publishing by hand then meant three
runner builds for a release that already existed, and the check that stopped it from overwriting
anything ran only after those builds finished.

## Manual GitHub Release upload

The GitHub UI can publish a release without using the workflow's `gh release create` step. Keep the
source tag and every native package on the same clean commit:

1. Push `main`, create the release tag (for example `v1.0.0`), and check out that exact tag on each
   target build machine.
2. **Delete `dist/` and `build/` before building.** Both are ignored by Git, so a working tree can
   hold artifacts from any earlier commit, and nothing in the upload step would notice.
3. Build the Windows Setup, Ubuntu/Debian DEB, and macOS DMG from that tag. Do not mix an older
   local `dist/` or `build/` artifact with a newer source tag; the Windows manifest records the
   exact source commit, and the `.sha256` sidecars name only the file, not its path.
4. In **GitHub → Releases → Draft a new release**, select the tag, set the title, and paste
   [`RELEASE_NOTES.ru.md`](../RELEASE_NOTES.ru.md) or [`RELEASE_NOTES.md`](../RELEASE_NOTES.md).
5. Upload these files (the two `.sha256` files and the Windows manifest are part of the release
   evidence):

   - `HoH-Setup-<version>.exe`
   - `HoH-Setup-<version>.manifest.json`
   - `hoh_<version>_amd64.deb`
   - `hoh_<version>_amd64.deb.sha256`
   - `HoH-<version>.dmg`
   - `HoH-<version>.dmg.sha256`

6. Leave **Set as the latest release** enabled, review the asset names and notes, then publish.

The repository's build workflow performs the same three builds on clean runners, but only when you
start it from **Actions → Build native packages → Run workflow**, and it publishes nothing. Use it
to check that the packaging scripts still work on a machine that is not yours; the release keeps the
packages you installed and exercised yourself.

## Cold-start verification

Install into a clean target profile. Do not copy local configuration, operational state, vendor
credentials, or cached Python paths into it.

```powershell
.\dist\HoH-Setup-1.0.0.exe /VERYSILENT /NORESTART
& "$env:LOCALAPPDATA\Programs\HoH\launch-hoh.ps1" doctor
& "$env:LOCALAPPDATA\Programs\HoH\launch-hoh.ps1" gui --smoke --locale ru --theme dark
```

```bash
sudo apt install ./dist/hoh_1.0.0_amd64.deb
hoh doctor
hoh-gui --smoke --locale ru --theme dark
```

On macOS, install `HoH.app` from the DMG into `/Applications`, start it from Finder, then run
`/Applications/HoH.app/Contents/MacOS/HoH doctor` for CLI evidence. Because the package is
ad-hoc signed, Gatekeeper blocks the first launch. On macOS 15 and newer, open
**System Settings → Privacy & Security** and press **Open Anyway** under the message naming HoH:
Control-click → **Open** stopped bypassing Gatekeeper in macOS 15, so the older instruction leaves a
user stuck. On earlier macOS, Finder → right-click `HoH.app` → **Open** still works. This is
expected for a local build and is not a Developer ID or notarization claim. Record the exact OS,
architecture, package hash, GUI smoke, uninstall/reinstall, and first-run wizard results separately
for every target. A Windows or Linux result says nothing about macOS.

Before public distribution, also run live `worker-conformance` for every bundled provider driver
that is advertised as operational in that release. The deterministic unknown-agent regression
proves the generic `command` boundary; it does not prove credentials or behavior of an external
provider CLI.

## Self-signed publisher and free static channel

Create the publisher once. The PFX is private and password protected; only the public JSON is
distributed to users:

```powershell
.\scripts\new-self-signed-publisher.ps1 -OutputDir "$env:LOCALAPPDATA\HoH\publisher"
.\scripts\hoh.ps1 publisher import --publisher "$env:LOCALAPPDATA\HoH\publisher\hoh-publisher.public.json"
```

Build a release payload with its final HTTPS download URL and sign it. A static host such as GitHub
Pages is sufficient; no paid catalog service is required:

```powershell
.\scripts\build-update-payload.ps1 `
  -ReleaseUrl "https://downloads.example/HoH-update-payload.zip" `
  -PublisherPfx "$env:LOCALAPPDATA\HoH\publisher\hoh-publisher.pfx"
```

The desktop **Project settings** page imports the public publisher JSON, checks the signed channel,
and installs a verified update beside the running version. Equivalent commands are:

```powershell
.\scripts\hoh.ps1 update check --source https://downloads.example/HoH-releases.signed.json
.\scripts\hoh.ps1 update install --source https://downloads.example/HoH-releases.signed.json
.\scripts\hoh.ps1 update status
.\scripts\hoh.ps1 update rollback
```

Unsigned, untrusted, tampered, wrong-platform, wrong-size, wrong-hash, non-HTTPS, archive traversal,
and symbolic-link packages fail closed. Installations live under
`%LOCALAPPDATA%\Programs\HoH\versions`; `current.json` changes only after extraction succeeds.

The same signed-envelope format carries central compatibility facts. Publish a signed payload based
on `catalogs/compatibility.payload.example.json`, then inspect it independently from local live
evidence:

```powershell
.\scripts\hoh.ps1 compatibility-matrix --project-root . `
  --central-catalog https://downloads.example/HoH-compatibility.signed.json --json
```

A central catalog describes what somebody else observed. Only `role-conformance` on this machine
says anything about this machine.

## Upgrade

1. Stop queue loops and Telegram scheduled polling for the installation being replaced.
2. Record `lock-status`, `reconcile-status`, `supervisor-status`, and the current release manifest.
3. Back up the repository-specific `.hoh-state/<repo-hash>` and `.hoh-leases/<repo-hash>` roots
   together. Preserve pending review bundles and operation records.
4. Verify the native installer hash or use a trusted signed release catalog for in-app updates.
5. Install the new Setup/DEB/DMG through the operating-system package flow.
6. Preserve operator-owned project configuration and environment setup; do not copy old runtime
   package directories.
7. Run doctor and read-only reconciliation against each managed repository before resuming work.
8. Keep the prior installer, manifest, configuration backup, and state backup until the new release has
   completed a representative queue cycle.

## Backup and restore

State and ledger evidence are one recovery unit:

- `.hoh-state/<repo-hash>` contains queue, history, journal, reviews, decisions, audits, rollback,
  Telegram offsets, and operator events;
- `.hoh-leases/<repo-hash>/operations` contains crash-reconciliation records;
- the canonical Git repository contains commits and the exact file history referenced by evidence.

Restore all three from a mutually consistent point. After restore, run `lock-status`,
`reconcile-status`, `supervisor-status`, and `doctor` before any mutating command. Never discard a
pending operation record merely to unblock the queue.

## Evidence retention

Retain release manifests and installer hashes for every handed-off build. Retain failed-run evidence when
it is needed to explain a production incident, safety decision, or live-provider limitation.
Deterministic stub conformance proves state-machine behavior but does not prove a live provider.
Likewise, a successful Worker commit with a pending Critic review is not a completed three-head run.

Workspace-specific preserve/archive/remove decisions are recorded in `docs/workspace-cleanup.md`.
