# HoH workspace cleanup

[Русский](workspace-cleanup.ru.md) · **English**

## Safety rule

Cleanup is evidence-driven. Before removal, verify active processes, scheduled tasks, Git
worktrees, configured state roots, reconciliation records, queue/review status, and whether the
item is the only copy of diagnostic evidence. Present the exact removal list to the operator and
obtain explicit approval. Do not infer approval from a general request to finish the project.

## Inventory snapshot

The inventory below describes the release-closing audit performed on 2026-07-20. Re-run the checks
before acting because process, task, queue, and filesystem state can change.

| Workspace item | Classification | Evidence and reason |
| --- | --- | --- |
| `harness/` | preserve | Canonical Git source, build, tests, docs, embedded runtime, wheelhouse, and release artifacts. |
| `.hoh-state/live-triad-smoke-*` | preserve | Unique queue/history/journal/review evidence; latest successful run remains `review_pending` with no imported decision. |
| `live-triad-smoke/` | preserve | Clean frozen Git repository containing the successful Hermes commit referenced by the pending review bundle. |
| `.hoh-leases/` | preserve while repository is managed | Runtime coordination root. The current harness lock file is metadata; OS lock state, not file age, determines activity. |
| `.hoh-attempts/` | removable when rechecked empty | Empty generated attempt root; HoH recreates it. No removal before operator approval. |
| `.tmp-live-critic/` | archive or remove after approval | Failed live Hermes/Claude test fixture; no commit or review decision. Useful only as negative provider evidence. |
| `.tmp-live-critic-stub/` | archive or remove after approval | Completed deterministic stub/Claude-identity fixture with an approved decision. It proves the review state machine, not a live Claude call. |
| `ralph/` | archive or remove after approval | Clean standalone upstream checkout used only as design reference; no runtime, build, package, process, task, or source reference. |
| Root design Markdown/PDF and `3headDevelopment.md` | archive or remove after approval | Superseded historical inputs; current contracts live under `harness/docs`. They are not runtime or package inputs. |
| `harness/build/`, `*.egg-info`, `__pycache__`, `.tmp/` | generated/removable | Build or interpreter caches; not tracked and recreated by build/test commands. |
| `harness/runtime/`, `vendor/`, `dist/` | preserve for release | Ignored generated production runtime and deliverables. Rebuild only through the documented scripts. |
| `harness/graphify-out/` | archive or remove after approval | Local analysis graph, not a build/runtime/package input. It may be valuable for future code archaeology. |

## Read-only recheck

```powershell
git -C C:\path\to\repo worktree list --porcelain
git -C C:\path\to\repo status --short
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*C:\path\to*' }
Get-ScheduledTask |
  Where-Object { $_.TaskName -like '*HoH*' -or $_.TaskPath -like '*HoH*' }
.\scripts\hoh.ps1 lock-status --project-root C:\path\to\repo --json
.\scripts\hoh.ps1 reconcile-status --project-root C:\path\to\repo --json
```

For every state root, also inspect queue, history, reviews, decisions, rollback, audits, and
reconciliation status using its matching canonical repository. If a canonical repository no
longer exists, preserve the state root until the orphan is understood and explicitly dispositioned.

## Approved cleanup execution

After explicit approval, remove only the named items whose classification and state were rechecked.
Use literal absolute paths and verify each path remains beneath `C:\path\to` before recursive
removal. Do not use broad wildcard deletion. Produce a post-cleanup inventory and repeat release
artifact hashes and the canonical clean-worktree audit.
