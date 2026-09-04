"""A scripted worker process for the live cycle test.

It reads the HoH task prompt on stdin and edits the isolated worktree, standing
in for a coding agent. The point is not intelligence: it is that a *separate
process* produces real edits to *existing* files, so the whole control plane is
exercised — worktree isolation, diff collection, the scope gate, verification,
the Supervisor-owned commit, the operation ledger and the final audit.

An in-process stub cannot do that, and a patch that only creates new files
cannot either: a new-file patch has no context lines, so it applies even when
the patch pipeline has mangled it.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys


QUOTE_AWARE_IMPLEMENTATION = '''"""Split a billing line into fields."""

from __future__ import annotations

import csv
from io import StringIO


def split_line(line: str) -> list[str]:
    """Split on commas, keeping commas that sit inside a double-quoted field."""
    if not line:
        return []
    return next(csv.reader(StringIO(line)))
'''

ADDED_TESTS = '''
    def test_quoted_field_keeps_its_comma(self):
        self.assertEqual(split_line('a,"b,c",d'), ["a", "b,c", "d"])

    def test_empty_line_has_no_fields(self):
        self.assertEqual(split_line(""), [])
'''

README_SECTION = """
## Quoted fields

A field wrapped in double quotes may contain commas:

```
a,"b,c",d  ->  ["a", "b,c", "d"]
```
"""


def main() -> int:
    prompt = sys.stdin.read()
    match = re.search(r"Task id:\s*(\S+)", prompt)
    task_id = match.group(1) if match else ""
    root = Path.cwd()

    if task_id == "quote-aware-split":
        (root / "src" / "calc" / "tokens.py").write_text(
            QUOTE_AWARE_IMPLEMENTATION, encoding="utf-8", newline="\n"
        )
        tests = root / "tests" / "test_tokens.py"
        tests.write_text(
            tests.read_text(encoding="utf-8").rstrip("\n") + "\n" + ADDED_TESTS,
            encoding="utf-8",
            newline="\n",
        )
        return 0

    if task_id == "document-quoting":
        readme = root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8").rstrip("\n") + "\n" + README_SECTION,
            encoding="utf-8",
            newline="\n",
        )
        return 0

    if task_id == "out-of-scope":
        # Deliberately writes outside allowed_paths. The gate must reject the patch
        # and this file must never appear in the canonical repository.
        (root / "secrets.env").write_text("API_KEY=leaked\n", encoding="utf-8", newline="\n")
        return 0

    print(f"Scripted worker has no plan for task {task_id!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
